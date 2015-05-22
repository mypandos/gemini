#!/usr/bin/env python
import collections
from copy import copy
import re
import GeminiQuery
import sql_utils
import compiler
from gemini_constants import *
from .gemini_bcolz import filter
import itertools as it
import operator as op


class GeminiInheritanceModel(object):

    required_columns = ("family_id", "family_members",
                        "family_genotypes", "samples", "family_count")

    def __init__(self, args):

        self.args = args
        self.gq = GeminiQuery.GeminiQuery(args.db, include_gt_cols=True)

        self.gt_cols = self.gq.gt_cols

        if not args.columns:
            args.columns = "*," + ", ".join(self.gt_cols)
        self.set_family_info()

    @property
    def query(self):
        if self.args.columns is not None:
            # the user only wants to report a subset of the columns
            query = "SELECT " + str(self.args.columns) + " FROM variants "
        else:
            # report the kitchen sink
            query = "SELECT chrom, start, end, * %s " \
                + "FROM variants" % ", ".join(self.gt_cols)

        query = sql_utils.ensure_columns(query, ['variant_id'])
        # add any non-genotype column limits to the where clause
        if self.args.filter:
            query += " WHERE " + self.args.filter

        # auto_rec and auto_dom candidates should be limited to
        # variants affecting genes.
        if self.model in ("auto_rec", "auto_dom") or \
           (self.model == "de_novo" and self.args.min_kindreds is not None):

            # we require the "gene" column for the auto_* tools
            query = sql_utils.ensure_columns(query, ['gene'])
            if self.args.filter:
                query += " AND gene is not NULL ORDER BY chrom, gene"
            else:
                query += " WHERE gene is not NULL ORDER BY chrom, gene"
        return query

    def bcolz_candidates(self):
        """
        Get all the variant ids that meet the genotype filter for any fam.
        """
        variant_ids = set()
        for i, family_id in enumerate(self.family_ids):
            gt_filter = self.family_masks[i]
            # TODO: maybe we should just or these together and call filter once.
            variant_ids.update(filter(self.args.db, gt_filter, {}))

        return sorted(set(variant_ids))

    def gen_candidates(self, group_key):
        if isinstance(group_key, basestring):
            group_key = op.itemgetter(group_key)

        q = self.query
        vids = self.bcolz_candidates()
        q = GeminiQuery.add_variant_ids_to_query(q, vids)
        self.gq.run(q, needs_genotypes=True)

        def update(gr):
            # gr is a gemini row
            return gr

        for grp_key, grp in it.groupby(self.gq, group_key):
            ogrp = (update(gr) for gr in grp)
            yield grp_key, ogrp

    def all_candidates(self):

        _, candidates = self.gen_candidates(group_key=None)
        for candidate in candidates:
            yield candidate

    def gene_candidates(self):

        for gene, candidates in self.gen_candidates(group_key="gene"):
            yield gene, candidates

    def set_family_info(self):
        """
        Extract the relevant genotype filters, as well all labels
        for each family in the database.
        """
        from .family import Family
        self.families = families = Family.from_cursor(self.gq.c).values()

        self.family_ids = []
        self.family_masks = []
        for family in families:
            # e.g. family.auto_rec(gt_ll, min_depth)
            family_filter = getattr(family,
                    self.model)(gt_ll=self.args.gt_phred_ll,
                                min_depth=self.args.min_sample_depth)

            self.family_masks.append(family_filter)
            self.family_ids.append(family.family_id)

    def report_candidates(self):
        req_cols = ['gt_types', 'gts']
        if self.args.min_sample_depth and self.args.min_sample_depth > 0:
            req_cols.append('gt_depths')
        if self.args.gt_phred_ll:
            req_cols.extend(['gt_phred_ll_homref', 'gt_phred_ll_het',
                             'gt_phred_ll_homalt'])

        masks = ['False' if m is None or m.strip('(').strip(')') == 'False'
                 else m for m in self.family_masks]
        masks = [compiler.compile(m, m, 'eval') for m in masks]

        for gene, li in self.candidates():
            li = list(li)
            if gene is not None:
                n_fams = len(frozenset(l['family_id'] for l in li))
                if n_fams < self.args.min_kindreds: continue

            for row in li:
                cols = dict((col, row[col]) for col in req_cols)
                fams = [f for i, f in enumerate(self.families)
                        if masks[i] != 'False' and eval(masks[i], cols)]

                # an ordered dict.
                pdict = row.print_fields

                for fam in fams:
                    # populate with the fields required by the tools.
                    pdict["family_id"] = fam.family_id
                    pdict["family_members"] = ",".join("%s" % m for m in fam.subjects)
                    pdict["family_genotypes"] = ",".join([eval(str(s), cols) for s in fam.gts])
                    pdict["samples"] = ",".join([x.name or x.sample_id for x in fam.subjects if x.affected])
                    pdict["family_count"] = len(fams)
                    yield pdict

    def run(self):
        for i, s in enumerate(self.report_candidates()):
            if i == 0:
                print "\t".join(s.keys())
            print "\t".join(map(str, s.values()))


class AutoDom(GeminiInheritanceModel):
    model = "auto_dom"

    def candidates(self):
        for g, li in self.gen_candidates('gene'):
            yield g, li


class AutoRec(AutoDom):
    model = "auto_rec"


class DeNovo(GeminiInheritanceModel):
    model = "de_novo"

    def candidates(self):
        kins = self.args.min_kindreds
        for g, li in self.gen_candidates('gene' if kins is not None else None):
            yield g, li


class MendelViolations(GeminiInheritanceModel):
    model = "mendel_violations"

    def candidates(self):
        for g, li in self.gen_candidates(None):
            yield g, li


class CompoundHet(GeminiInheritanceModel):
    model = "comp_het"

    @property
    def query(self):
        args = self.args
        if args.columns is not None:
            custom_columns = self._add_necessary_columns(str(args.columns))
            query = "SELECT " + custom_columns + \
                    " FROM variants " + \
                    " WHERE (is_exonic = 1 or impact_severity != 'LOW') "
        else:
            # report the kitchen sink
            query = "SELECT *" + \
                    ", gts, gt_types, gt_phases, gt_depths, \
                    gt_ref_depths, gt_alt_depths, gt_quals" + \
                    " FROM variants " + \
                    " WHERE (is_exonic = 1 or impact_severity != 'LOW') "

        if args.filter: query += " AND " + args.filter
        # we need to order results by gene so that we can sweep through the results
        return query + " ORDER BY gene"

    def _add_necessary_columns(self, custom_columns):
        """
        Convenience function to tack on columns that are necessary for
        the functionality of the tool but yet have not been specifically
        requested by the user.
        """
        # we need to add the variant's chrom, start and gene if
        # not already there.
        self.added = []
        for col in ("gene", "start", "alt", "variant_id"):
            if custom_columns.find(col) < 0:
                custom_columns += "," + col
                if col != "variant_id":
                    self.added.append(col)
        return custom_columns

    def find_valid_het_pairs(self, sample_hets):
        """
        Identify candidate heterozygote pairs.
        """
        args = self.args
        samples_w_hetpair = collections.defaultdict(list)
        splitter = re.compile("\||/")
        for sample in sample_hets:
            for gene in sample_hets[sample]:

                # we only care about combinations, not permutations
                # (e.g. only need site1,site2, not site1,site2 _and site2,site1)
                # thus we can do this in a ~ linear pass instead of a ~ N^2 pass
                for idx, site1 in enumerate(sample_hets[sample][gene]):
                    for site2 in sample_hets[sample][gene][idx + 1:]:

                        # expand the genotypes for this sample at each site into
                        # it's composite alleles.  e.g. A|G -> ['A', 'G']
                        alleles_site1 = []
                        alleles_site2 = []
                        if not args.ignore_phasing:
                            alleles_site1 = site1.gt.split('|')
                            alleles_site2 = site2.gt.split('|')
                        else:
                            # split on phased (|) or unphased (/) genotypes
                            alleles_site1 = splitter.split(site1.gt)
                            alleles_site2 = splitter.split(site2.gt)

                        # it is only a true compound heterozygote IFF
                        # the alternates are on opposite haplotypes.
                        if not args.ignore_phasing:
                            # return the haplotype on which the alternate allele
                            # was observed for this sample at each candidate het.
                            # site. e.g., if ALT=G and alleles_site1=['A', 'G']
                            # then alt_hap_1 = 1.  if ALT=A, then alt_hap_1 = 0
                            if "," in str(site1.row['alt']) or \
                               "," in str(site2.row['alt']):
                                sys.stderr.write("WARNING: Skipping candidate for sample"
                                                 " %s b/c variants with mult. alt."
                                                 " alleles are not yet supported. The sites are:"
                                                 " %s and %s.\n" % (sample, site1, site2))
                                continue

                            alt_hap_1 = alleles_site1.index(site1.row['alt'])
                            alt_hap_2 = alleles_site2.index(site2.row['alt'])

                        # Keep as a candidate if
                        #   1. phasing is considered AND the alt alleles are on
                        #      different haplotypes
                        #   2. the user doesn't care about phasing.
                        # TODO: Phase based on parental genotypes.
                        if (not args.ignore_phasing and alt_hap_1 != alt_hap_2) \
                            or args.ignore_phasing:
                            samples_w_hetpair[(site1,site2)].append(sample)

        return samples_w_hetpair


    def filter_candidates(self, samples_w_hetpair,
                          comp_het_counter=[0]):
        """
        Refine candidate heterozygote pairs based on user's filters.
        """
        args = self.args
        # eliminate comp_hets with unaffected individuals if
        # only affected individuals are required.
        # once we are in here, we know that we have a single gene.
        from .family import Family
        self.gq._connect_to_database()
        fams = Family.from_cursor(self.gq.c)
        subjects_dict = {}
        for f in fams:
            for s in fams[f].subjects:
                subjects_dict[s.name] = s

        candidates = {}
        if args.only_affected:
            for comp_het in samples_w_hetpair:
                num_affected = 0
                for fam in fams:
                    num_affected += [1 for s in f.subjects if s.affected]

                # NOTE: testing for exact number here. what if 1 doesn't have it?
                if num_affected == len(samples_w_hetpair[comp_het]):
                    candidates[comp_het] = samples_w_hetpair[comp_het]
        else:
            candidates = samples_w_hetpair

        # catalog the set of families that have a comp_het in this gene
        family_count = collections.Counter()
        for comp_het in candidates:
            for s in samples_w_hetpair[comp_het]:
                family_id = subjects_dict[s].family_id
                family_count[family_id] += 1

        # were there enough families with a compound het in this gene?
        # keys of (variant_id, gene) vals of [row, family_gt_label, family_gt_cols,
        # family_id, comp_het_id]
        if len(family_count) >= args.min_kindreds:
            for idx, comp_het in enumerate(candidates):
                comp_het_counter[0] += 1
                for s in samples_w_hetpair[comp_het]:
                    family_id = subjects_dict[s].family_id
                    if args.families is not None and family_id not in args.families.split(','):
                        continue

                    ch_id = str(comp_het_counter[0])
                    for i in (0, 1):

                        pdict = comp_het[i].row.print_fields
                        # set these to keep order in the ordered dict.
                        pdict["family_id"] = None
                        pdict["family_members"] = None
                        pdict["family_genotypes"] = None
                        pdict["samples"] = None
                        pdict["family_count"] = None
                        pdict["comp_het_id"] = "%s_%s" % (pdict['variant_id'], str(ch_id))
                        yield comp_het[i].row['gene'], [comp_het[i].row]

    def candidates(self):
        idx_to_sample = self.gq.idx_to_sample

        for grp, li in self.gen_candidates('gene'):
            sample_hets = collections.defaultdict(lambda: collections.defaultdict(list))
            for row in li:

                gt_types, gt_bases, gt_phases = row['gt_types'], row['gts'], row['gt_phases']
                site = Site(row)
                # track each sample that is heteroyzgous at this site.
                for idx, gt_type in enumerate(gt_types):
                    if gt_type != HET:
                        continue
                    sample = idx_to_sample[idx]
                    sample_site = copy(site)
                    sample_site.phased = gt_phases[idx]

                    if not sample_site.phased and not args.ignore_phasing:
                        continue

                    sample_site.gt = gt_bases[idx]
                    # add the site to the list of candidates for this sample/gene
                    sample_hets[sample][site.row['gene']].append(sample_site)

            # process the last gene seen
            samples_w_hetpair = self.find_valid_het_pairs(sample_hets)
            for d in self.filter_candidates(samples_w_hetpair):
                yield d

class Site(object):
    def __init__(self, row):
        self.row = row
        self.phased = None
        self.gt = None

    def __eq__(self, other):
        return self.row['chrom'] == other.row['chrom'] and \
               self.row['start'] == other.row['start']

    def __repr__(self):
        return ",".join([self.row['chrom'],
                         str(self.row['start']),
                         str(self.row['end'])])

    def __hash__(self):
        "hash the site based on chrom+start"
        return sum(ord(c) for c in self.row['chrom']) + int(self.row['start'])
