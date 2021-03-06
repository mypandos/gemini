# wget ftp://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf/clinvar_20130118.vcf.gz
# wget ftp://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf/clinvar_20130118.vcf.gz.tbi

# wget ftp://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf/clinvar_20131230.vcf.gz
# wget ftp://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf/clinvar_20131230.vcf.gz.tbi

# wget ftp://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf/clinvar_20140303.vcf.gz
# wget ftp://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf/clinvar_20140303.vcf.gz.tbi

#wget ftp://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh37/clinvar_20140807.vcf.gz
#wget ftp://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh37/clinvar_20140807.vcf.gz.tbi

#wget  ftp://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh37/clinvar_20150305.vcf.gz \
#tabix clinvar_20150305.vcf.gz

DATE=20170130

wget -O cv.vcf.gz ftp://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh37/archive/2017/clinvar_${DATE}.vcf.gz

vt decompose -s cv.vcf.gz \
		   | python clinvar.py \
		   | vt normalize -r /data/human/human_g1k_v37.fasta - \
		   | bgzip -c > clinvar_$DATE.tidy.vcf.gz
tabix clinvar_$DATE.tidy.vcf.gz
