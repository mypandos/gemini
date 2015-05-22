"""
Create filters for given inheritance models.
See: https://github.com/arq5x/gemini/issues/388
"""
from collections import defaultdict
import operator as op
import sys
try:
    reduce
except NameError:
    from functools import reduce

HOM_REF, HET, UNKNOWN, HOM_ALT = range(4)

valid_gts = (
    'gts',
    'gt_types',
    'gt_phases',
    'gt_depths',
    'gt_ref_depths',
    'gt_alt_depths',
    'gt_quals',
    'gt_copy_numbers',
    'gt_phred_ll_homref',
    'gt_phred_ll_het',
    'gt_phred_ll_homalt',
)

def _bracket(other):
    o = str(other)
    if o in ("True", "False"): return o
    return "(%s)" % o

class ostr(str):
    def __and__(self, other):
        if other is None: return self
        if other is empty: return self
        if self is empty: return other
        return ostr("%s and %s" % (_bracket(self), _bracket(other)))

    def __or__(self, other):
        if other is None: return self
        if other is empty: return self
        if self is empty: return other
        return ostr("%s or %s" % (_bracket(self), _bracket(other)))

    def __nonzero__(self):
        raise Exception("shouldn't be here. use & instead of 'and'. and wrap in parens")

empty = ostr("empty")

class Sample(object):
    """
    >>> a, b = Sample(1, True), Sample(2, True)
    >>> a.gt_types == HOM_REF
    'gt_types[0] == HOM_REF'

    >>> a.gt_types == b.gt_types
    'gt_types[0] == gt_types[1]'

    >>> a.gt_types != b.gt_types
    'gt_types[0] != gt_types[1]'

    >>> (a.gt_phred_ll_homref < 2) & (a.gt_phred_ll_homalt > 2)
    '(gt_phred_ll_homref[0] < 2) and (gt_phred_ll_homalt[0] > 2)'

    >>> (a.gt_phred_ll_homref < 2) | (b.gt_phred_ll_homalt > 2)
    '(gt_phred_ll_homref[0] < 2) or (gt_phred_ll_homalt[1] > 2)'
    """

    __slots__ = ('sample_id', 'name', 'affected', 'gender', 'mom', 'dad',
                 'family_id', '_i')

    def __init__(self, sample_id, affected, gender=None, name=None,
                 family_id=None):
        #assert isinstance(sample_id, (long, int)), sample_id
        assert affected in (True, False, None)
        self.sample_id = sample_id
        self.name = name or sample_id
        self.affected = affected
        self.mom = None
        self.dad = None
        self.gender = gender
        self.family_id = family_id
        # _i is used to maintain the order in which they came in.
        self._i = None

    def __getattr__(self, gt_field):
        assert gt_field in valid_gts, gt_field
        return Filter(self.sample_id, gt_field)

    def __repr__(self):
        c = self.__class__.__name__
        s = "%s(%s" % (c, self.name or self.sample_id)
        s += (";affected" if self.affected else (";unaffected"
                  if self.affected is False else ";unknown"))
        if self.gender is not None:
            s += ";sex=%s" % self.gender
        return s + ")"

    def __str__(self):
        r = repr(self).split("(", 1)[1]
        return "%s(%s" % (self.name, r)


class Filter(object):
    def __init__(self, sample_id, gt_field):
        self.gt_field = gt_field
        if isinstance(sample_id, (long, int)):
            self.sample0 = sample_id - 1
        else:
            self.sample0 = sample_id

    def in_(self, li):
        return reduce(op.or_, [self == i for i in li])

    def __lt__(self, o):
        return ostr("%s[%s] < %s" % (self.gt_field, self.sample0, o))

    def __le__(self, o):
        return ostr("%s[%s] <= %s" % (self.gt_field, self.sample0, o))

    def __gt__(self, o):
        return ostr("%s[%s] > %s" % (self.gt_field, self.sample0, o))

    def __ge__(self, o):
        return ostr("%s[%s] >= %s" % (self.gt_field, self.sample0, o))

    def __eq__(self, o):
        return ostr("%s[%s] == %s" % (self.gt_field, self.sample0, o))

    def __ne__(self, o):
        return ostr("%s[%s] != %s" % (self.gt_field, self.sample0, o))

    def __str__(self):
        return ostr("%s[%s]" % (self.gt_field, self.sample0))

    def __and__(self, other):
        raise Exception("shouldn't be here. wrap &/| statements in parens")

    __or__ = __and__

    def __nonzero__(self):
        raise Exception("shouldn't be here. use & instead of 'and'")

class Family(object):
    """
    >>> mom = Sample(1, affected=False)
    >>> dad = Sample(2, affected=False)
    >>> kid = Sample(3, affected=True)
    >>> Family([mom, dad, kid], 'a').auto_rec()
    '(gt_types[2] == HOM_ALT) and ((gt_types[0] != HOM_ALT) and (gt_types[1] != HOM_ALT))'

    # unknowns don't count
    >>> kid2 = Sample(4, affected=None)

    >>> Family([mom, dad, kid, kid2], 'a').auto_rec()
    '(gt_types[2] == HOM_ALT) and ((gt_types[0] != HOM_ALT) and (gt_types[1] != HOM_ALT))'

    >>> Family([mom, dad, kid, kid2], 'a').auto_rec(gt_ll=1, min_depth=10)
    '(((gt_types[2] == HOM_ALT) and (gt_phred_ll_homalt[2] <= 1)) and (((gt_types[0] != HOM_ALT) and (gt_types[1] != HOM_ALT)) and ((gt_phred_ll_homalt[0] > 1) and (gt_phred_ll_homalt[1] > 1)))) and ((gt_depths[2] >= 10) and ((gt_depths[0] >= 10) and (gt_depths[1] >= 10)))'

    """

    def __init__(self, subjects, fam_id):
        assert len(set(s.sample_id for s in subjects)) == len(subjects), subjects
        self.subjects = subjects
        self.family_id = fam_id

    @classmethod
    def from_ped(klass, ped):
        """
        return a dict keyed by family_id with parent/kid relations defined from a ped file.
        """
        def agen():
            for toks in (l.rstrip().split() for l in open(ped) if l[0] != "#"):
                toks.append(toks[1]) # name
                yield toks
        return klass._from_gen(agen())

    def __len__(self):
        return len(self.subjects)

    @classmethod
    def from_cursor(klass, cursor):
        keys = "sample_id|family_id|name|paternal_id|maternal_id|sex|phenotype".split("|")
        def agen():
            for row in cursor.execute("select %s from samples" % ",".join(keys)):
                if not isinstance(row, dict):
                    row = dict(zip(keys, row))
                yield (row['family_id'], row['sample_id'], row['paternal_id'],
                       row['maternal_id'], row['sex'], str(row['phenotype']),
                       row['name'])
        return klass._from_gen(agen())

    @classmethod
    def _from_gen(klass, gen):
        fams = defaultdict(dict)
        pheno_lookup = {'1': False, '2': True}
        gender_lookup = {'1': 'male', '2': 'female'}
        for i, (fam_id, indv, pat_id, mat_id, sex, pheno, name) in enumerate(gen):

            assert indv not in fams[fam_id]
            s = fams[fam_id][name] = Sample(indv, pheno_lookup.get(pheno),
                                        gender=gender_lookup.get(sex))
            s.mom = mat_id
            s.dad = pat_id
            # name in gemini is actually the id from the ped.
            # sample_id in gemini si the primary key
            s.name = name
            s.family_id = fam_id
            s._i = i

        ofams = {}
        for fam_id, fam_dict in fams.items():
            ofams[fam_id] = []
            for name in fam_dict:
                sample = fam_dict[name]
                # convert sample_id to dad or None
                sample.dad = fam_dict.get(sample.dad)
                sample.mom = fam_dict.get(sample.mom)
                ofams[fam_id].append(sample)

            ofams[fam_id] = Family(ofams[fam_id], fam_id)
            # maintain the order in which they came in.
            ofams[fam_id].subjects.sort(key=op.attrgetter('_i'))
        return ofams

    def __repr__(self):
        return "%s([%s])" % (self.__class__.__name__,
                           ", ".join(repr(s) for s in self.subjects))

    @property
    def affecteds(self):
        return [s for s in self.subjects if s.affected]

    @property
    def affecteds_with_parent(self):
        return [s for s in self.affecteds if not None in (s.mom, s.dad)]

    @property
    def unaffecteds(self):
        return [s for s in self.subjects if s.affected is False]

    @property
    def unknown(self):
        return [s for s in self.subjects if s.affected is None]

    # so we can do, e.g. fam.gts and get the list of required strings.
    def __getattr__(self, gt_field):
        assert gt_field in valid_gts, gt_field
        return [getattr(s, gt_field) for s in self.subjects]

    def _restrict_to_min_depth(self, min_depth):
        if min_depth is not None and min_depth > 0:
            af = reduce(op.and_, [s.gt_depths >= min_depth for s in self.affecteds])
            un = reduce(op.and_, [s.gt_depths >= min_depth for s in self.unaffecteds])
            return af & un
        else:
            return None

    def auto_dom(self, min_depth=0, gt_ll=False, strict=True, affected_only=True):
        """
        At least 1 affected child must have at least 1 affected/unknown parent.
        If strict then all affected kids must have at least 1 affected parent.
        parent.
        """
        if len(self.affecteds) == 0:
            return 'False'
        af = reduce(op.and_, [s.gt_types == HET for s in self.affecteds])
        if len(self.unaffecteds) and affected_only:
            un = reduce(op.and_, [(s.gt_types != HET) & (s.gt_types != HOM_ALT) for s in self.unaffecteds])
        else:
            un = None
        depth = self._restrict_to_min_depth(min_depth)
        if gt_ll:
            af &= reduce(op.and_, [s.gt_phred_ll_het <= gt_ll for s in self.affecteds])
            if len(self.unaffecteds) and affected_only:
                un &= reduce(op.and_, [s.gt_phred_ll_het > gt_ll for s in self.unaffecteds])

        if strict:
            # all affected kids must have at least 1 affected parent (or no parents)
            kid_with_parents = False
            for kid in self.affecteds:
                # if they have no parents, don't require it
                if kid.mom is None and kid.dad is None:
                    continue
                # mom or dad must be affected.
                kid_with_parents = True
                if not any(p is not None and p.affected for p in (kid.mom, kid.dad)):
                    return 'False'
            if not kid_with_parents:
                sys.stderr.write("WARNING: family %s had no usable samples for"
                                 " autosomal dominant test\n" % self.family_id)

        return af & un & depth

    def auto_rec(self, min_depth=0, gt_ll=False, strict=True, affected_only=True):
        """
        If strict, then if parents exist, they must be het for all affecteds
        """
        af = reduce(op.and_, [s.gt_types == HOM_ALT for s in self.affecteds])
        if affected_only:
            un = reduce(op.and_, [s.gt_types != HOM_ALT for s in self.unaffecteds])
        else:
            un = None
        if strict:
            # if parents exist, they must be het or affected for all affecteds
            # if both parents are not het then it's a de novo.
            usable_kid = False
            for kid in self.affecteds:
                usable_kid = usable_kid or (kid.mom and kid.dad)
                for parent in (kid.mom, kid.dad):
                    if parent is not None:
                        af &= parent.gt_types == HET
                        if parent.affected:
                            sys.stderr.write("WARNING: auto-recessive called on family "
                                    "%s where affected has affected parents\n" % self.family_id)
                            return "False"
                if not usable_kid:
                    sys.stderr.write("WARNING: auto-recessive called on family "
                            "%s where no affected has parents\n" % self.family_id)

        depth = self._restrict_to_min_depth(min_depth)
        if gt_ll:
            af &= reduce(op.and_, [s.gt_phred_ll_homalt <= gt_ll for s in self.affecteds])
            if affected_only:
                un &= reduce(op.and_, [s.gt_phred_ll_homalt > gt_ll for s in self.unaffecteds])

        return af & un & depth

    def de_novo(self, min_depth=0, gt_ll=False, strict=True, affected_only=True):
        """
        all affected must be het.
        all unaffected must be homref.
        if strict, all affected kids must have unaffected parents.
        >>> f = Family([Sample("mom", False), Sample("dad", False),
        ...             Sample("kid", True)], "fam")
        """
        if len(self.affecteds) == 0:
            return 'False'
        af = reduce(op.and_, [s.gt_types == HET for s in self.affecteds])
        un = empty
        if affected_only:
            un = reduce(op.and_, [s.gt_types == HOM_REF for s in self.unaffecteds])
        if gt_ll:
            af &= reduce(op.and_, [s.gt_phred_ll_het <= gt_ll for s in self.affecteds])
            if affected_only:
                un &= reduce(op.and_, [s.gt_phred_ll_homref <= gt_ll for s in self.unaffecteds])

        if affected_only:
            un2 = reduce(op.and_, [s.gt_types == HOM_ALT for s in self.unaffecteds])
            if gt_ll:
                un2 &= reduce(op.and_, [s.gt_phred_ll_homalt <= gt_ll for s in self.unaffecteds])
            un |= un2

        depth = self._restrict_to_min_depth(min_depth)
        if strict:
            # if a parent is affected it's not de novo.
            for kid in self.affecteds:
                for parent in (kid.mom, kid.dad):
                    if parent is not None and parent.affected:
                        return 'False'
        return af & un & depth

    def mendel_plausible_denovo(self, min_depth=0, gt_ll=False):
        """
        kid == HET and dad, mom == HOM_REF or dad, mom == HOM_ALT
        only use kids with both parents present.
        """
        subset = self.affecteds_with_parent
        if len(subset) == 0: return 'False'
        depth = self._restrict_to_min_depth(min_depth)
        # join exprs by or. so just look for any kid that meets these within a family.
        exprs = []
        for s in subset:
            # kid het, parents hom_ref
            expr = (s.gt_types == HET) & (s.mom.gt_types == HOM_REF) & (s.dad.gt_types == HOM_REF)
            if gt_ll:
                expr &= (s.gt_phred_ll_het <= gt_ll) & (s.mom.gt_phred_ll_homref <= gt_ll) & (s.dad.gt_phred_ll_homref <= gt_ll)
            exprs.append(expr)
            # kid het, parents hom_alt
            expr = (s.gt_types == HET) & (s.mom.gt_types == HOM_ALT) & (s.dad.gt_types == HOM_ALT)
            if gt_ll:
                expr &= (s.gt_phred_ll_het <= gt_ll) & (s.mom.gt_phred_ll_homalt <= gt_ll) & (s.dad.gt_phred_ll_homalt <= gt_ll)
            exprs.append(expr)
        return reduce(op.or_, exprs) & depth

    def mendel_implausible_denovo(self, min_depth=0, gt_ll=False):
        # everyone is homozygote. kid is opposit of parents.
        # only use kids with both parents present.
        subset = self.affecteds_with_parent
        if len(subset) == 0: return 'False'
        depth = self._restrict_to_min_depth(min_depth)
        exprs = []
        for s in subset:
            # kid hom_alt, parents hom_ref
            expr = (s.gt_types == HOM_ALT) & (s.mom.gt_types == HOM_REF) & (s.dad.gt_types == HOM_REF)
            if gt_ll:
                expr &= (s.gt_phred_ll_homalt <= gt_ll) & (s.mom.gt_phred_ll_homref <= gt_ll) & (s.dad.gt_phred_ll_homref <= gt_ll)
            exprs.append(expr)
            # parents hom_alt kid homref
            expr = (s.gt_types == HOM_REF) & (s.mom.gt_types == HOM_ALT) & (s.dad.gt_types == HOM_ALT)
            if gt_ll:
                expr &= (s.gt_phred_ll_homref <= gt_ll) & (s.mom.gt_phred_ll_homalt <= gt_ll) & (s.dad.gt_phred_ll_homalt <= gt_ll)
            exprs.append(expr)
        return reduce(op.or_, exprs) & depth

    def mendel_uniparental_disomy(self, min_depth=0, gt_ll=False):
        # parents are opposite homs, kid matches one of them (but should be
        # het).
        subset = self.affecteds_with_parent
        if len(subset) == 0: return 'False'
        depth = self._restrict_to_min_depth(min_depth)
        # join exprs with or
        exprs = []
        for s in subset:
            for gtkid in (HOM_REF, HOM_ALT):
                # mom homref, dad hom_alt.
                expr = (s.gt_types == gtkid) & (s.mom.gt_types == HOM_REF) & (s.dad.gt_types == HOM_ALT)
                if gt_ll:
                    if gtkid == HOM_REF:
                        expr &= (s.gt_phred_ll_homref <= gt_ll)
                    else:
                        expr &= (s.gt_phred_ll_homalt <= gt_ll)
                    expr &= (s.mom.gt_phred_ll_homref <= gt_ll) & (s.dad.gt_phred_ll_homalt <= gt_ll)
                exprs.append(expr)

                # mom homalt, dad hom_ref.
                expr = (s.gt_types == gtkid) & (s.dad.gt_types == HOM_REF) & (s.mom.gt_types == HOM_ALT)
                if gt_ll:
                    if gtkid == HOM_REF:
                        expr &= (s.gt_phred_ll_homref <= gt_ll)
                    else:
                        expr &= (s.gt_phred_ll_homalt <= gt_ll)
                    expr &= (s.dad.gt_phred_ll_homref <= gt_ll) & (s.mom.gt_phred_ll_homalt <= gt_ll)
                exprs.append(expr)

        return reduce(op.or_, exprs) & depth

    def mendel_LOH(self, min_depth=0, gt_ll=False):
        # kid and one parent are opposite homozygotes other parent is het.
        subset = self.affecteds_with_parent
        if len(subset) == 0: return 'False'
        depth = self._restrict_to_min_depth(min_depth)

        exprs = []  # joined with or
        for s in subset:
            # kid hom_alt, mom hom_ref, dad het.
            e = (s.gt_types == HOM_ALT) & (s.mom.gt_types == HOM_REF) & (s.dad.gt_types == HET)
            if gt_ll:
                e &= (s.gt_phred_ll_homalt <= gt_ll) & (s.mom.gt_phred_ll_homref <= gt_ll) & (s.dad.gt_phred_ll_het <= gt_ll)
            exprs.append(e)

            # kid hom_ref, mom het, dad hom_alt
            e = (s.gt_types == HOM_REF) & (s.mom.gt_types == HET) & (s.dad.gt_types == HOM_ALT)
            if gt_ll:
                e &= (s.gt_phred_ll_homref <= gt_ll) & (s.mom.gt_phred_ll_het <= gt_ll) & (s.dad.gt_phred_ll_homalt <= gt_ll)
            exprs.append(e)

            # kid hom_alt, mom het, dad hom_ref
            e = (s.gt_types == HOM_ALT) & (s.mom.gt_types == HET) & (s.dad.gt_types == HOM_REF)
            if gt_ll:
                e &= (s.gt_phred_ll_homalt <= gt_ll) & (s.mom.gt_phred_ll_het <= gt_ll) & (s.dad.gt_phred_ll_homref <= gt_ll)
            exprs.append(e)

            # kid hom_ref, mom hom_alt, dad het
            e = (s.gt_types == HOM_REF) & (s.mom.gt_types == HOM_ALT) & (s.dad.gt_types == HET)
            if gt_ll:
                e &= (s.gt_phred_ll_homref <= gt_ll) & (s.mom.gt_phred_ll_homalt <= gt_ll) & (s.dad.gt_phred_ll_het <= gt_ll)
            exprs.append(e)

        return reduce(op.or_, exprs) & depth

    def mendel_violations(self, min_depth=0, gt_ll=False):
        """
        >>> f = Family([Sample("mom", False), Sample("dad", False),
        ...             Sample("kid", True)], "fam")
        >>> f.subjects[2].mom = f.subjects[0]
        >>> f.subjects[2].dad = f.subjects[1]
        >>> r = f.mendel_violations()
        >>> for k in r:
        ...     print k
        ...     print r[k]
        uniparental disomy
        (((((gt_types[kid] == HOM_REF) and (gt_types[mom] == HOM_REF)) and (gt_types[dad] == HOM_ALT)) or (((gt_types[kid] == HOM_REF) and (gt_types[dad] == HOM_REF)) and (gt_types[mom] == HOM_ALT))) or (((gt_types[kid] == HOM_ALT) and (gt_types[mom] == HOM_REF)) and (gt_types[dad] == HOM_ALT))) or (((gt_types[kid] == HOM_ALT) and (gt_types[dad] == HOM_REF)) and (gt_types[mom] == HOM_ALT))
        plausible de novo
        (((gt_types[kid] == HET) and (gt_types[mom] == HOM_REF)) and (gt_types[dad] == HOM_REF)) or (((gt_types[kid] == HET) and (gt_types[mom] == HOM_ALT)) and (gt_types[dad] == HOM_ALT))
        loss of heterozygosity
        (((((gt_types[kid] == HOM_ALT) and (gt_types[mom] == HOM_REF)) and (gt_types[dad] == HET)) or (((gt_types[kid] == HOM_REF) and (gt_types[mom] == HET)) and (gt_types[dad] == HOM_ALT))) or (((gt_types[kid] == HOM_ALT) and (gt_types[mom] == HET)) and (gt_types[dad] == HOM_REF))) or (((gt_types[kid] == HOM_REF) and (gt_types[mom] == HOM_ALT)) and (gt_types[dad] == HET))
        implausible de novo
        (((gt_types[kid] == HOM_ALT) and (gt_types[mom] == HOM_REF)) and (gt_types[dad] == HOM_REF)) or (((gt_types[kid] == HOM_REF) and (gt_types[mom] == HOM_ALT)) and (gt_types[dad] == HOM_ALT))
        """
        return {'plausible de novo': self.mendel_plausible_denovo(min_depth, gt_ll),
                'implausible de novo': self.mendel_implausible_denovo(min_depth, gt_ll),
                'uniparental disomy': self.mendel_uniparental_disomy(min_depth, gt_ll),
                'loss of heterozygosity': self.mendel_LOH(min_depth, gt_ll)
                }

    def comp_het(self, min_depth=0, gt_ll=False, strict=False,
                 affected_only=True):
        """
        affecteds are het.
        unaffecteds are not hom_alt
        (later remove candidates if unaffected share same het pair)
        """
        af = reduce(op.or_, [s.gt_types == HET for s in self.affecteds], empty)

        if affected_only:
            un = reduce(op.and_, [s.gt_types != HOM_ALT for s in self.unaffecteds], empty)

        depth = self._restrict_to_min_depth(min_depth)
        if not strict:
            af &= reduce(op.or_, [s.gt_types == HET for s in self.unknown], empty)

        if gt_ll:
            af &= reduce(op.and_, [s.gt_phred_ll_het <= gt_ll for s in self.affecteds])
            if affected_only:
                un &= reduce(op.and_, [s.gt_phred_ll_homalt > gt_ll for s in self.unaffecteds])

        return af & un & depth

if __name__ == "__main__":

    HOM_REF, HET, UNKNOWN, HOM_ALT = "HOM_REF HET UNKNOWN HOM_ALT".split()
    import doctest
    import sys
    sys.stderr.write(str(doctest.testmod(optionflags=
                     doctest.NORMALIZE_WHITESPACE
                     | doctest.ELLIPSIS
                     | doctest.REPORT_ONLY_FIRST_FAILURE, verbose=0)) + "\n")

    if len(sys.argv) > 1:
        mom = Sample('mom', False)
        dad = Sample('dad', False)
        kid = Sample('kid', True)
        kid.mom = mom
        kid.dad = dad
        fam = Family([mom, dad, kid], 'fam')

        me = fam.mendel_violations()
        for k in me:
            print k
            print me[k]
            print


        print "auto recessive:"
        print fam.auto_rec(min_depth=10), "\n"

        print "auto dom:"
        print fam.auto_dom()
        print fam.auto_dom(min_depth=10)

        import sqlite3
        db = sqlite3.connect('test/test.auto_rec.db')
        fams_ped = Family.from_ped('test/test.auto_rec.ped')
        print fams_ped
        1/0
        fams_db = Family.from_cursor(db)
        print "auto_rec:"
        for famid, fam in fams_ped.items():
            print famid
            print fam.auto_rec()

        print "de novo:"
        for famid, fam in fams_ped.items():
            print fam.de_novo()
            break

