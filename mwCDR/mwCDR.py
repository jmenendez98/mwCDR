import argparse
import concurrent.futures
import os

import numpy as np
from scipy.stats import mannwhitneyu

from typing import Dict, Optional, List, Union

from hmmCDR.bed_parser import bed_parser


class calculate_matrices:
    def __init__(
        self,
        window_size,
        step_size,
        high_conf_p,
        low_conf_p,
        min_size,
        merge_distance,
        enrichment,
        threads,
        output_label,
    ):

        self.window_size = window_size
        self.step_size = step_size
        self.high_conf_p = high_conf_p
        self.low_conf_p = low_conf_p

        self.min_size = min_size # base pairs
        self.merge_distance = merge_distance # base pairs

        self.enrichment = enrichment

        self.hc_color = "50,50,255"
        self.lc_color = "150,150,150"

        self.threads = threads
        self.output_label = output_label

    def calculate_regional_stats(self, methylation):
        methyl_starts = np.array(methylation["starts"], dtype=int)
        methyl_frac_mod = np.array(methylation["fraction_modified"], dtype=float)

        p_values = np.array([np.ones(self.window_size) for _ in range(len(methyl_starts))])

        for i in range(0, len(methyl_starts), self.step_size):
            start = max(0, i - (self.window_size//2))
            end = min(len(methyl_starts), i + (self.window_size//2) + 1)

            current_region_frac_mods = methyl_frac_mod[start:end]
            
            alt = "less" if not self.enrichment else "greater"
            _, p_value = mannwhitneyu(current_region_frac_mods, methyl_frac_mod, alternative=alt, nan_policy="omit")
            for j in range(self.window_size):
                p_values[i][j] = p_value

        methylation["mannU_p_value"] = [np.median(ps) for ps in p_values]

        return methylation

    def find_priors(self, methylation):
        methyl_starts = np.array(methylation["starts"], dtype=int)
        methyl_p_values = np.array(methylation["mannU_p_value"], dtype=float)

        # create an array to store confidence levels (0: no confidence, 1: low confidence, 2: high confidence)
        mdr_conf = np.zeros(len(methyl_p_values), dtype=int)
        mdr_conf = np.where(np.array(methyl_p_values) <= self.low_conf_p, 1, mdr_conf)
        mdr_conf = np.where(np.array(methyl_p_values) <= self.high_conf_p, 2, mdr_conf)
        
        mdrs = {"starts": [], "ends": [], "names": [], "scores": [], "strands": [], "itemRgbs": []}
        
        # helper function to add entries to the mdrs dictionary
        def create_entry_helper(start, end, name, scores, color):
            mdrs["starts"].append(start)
            mdrs["ends"].append(end)
            mdrs["names"].append(name)
            mdrs["scores"].append(np.median(scores))
            mdrs["strands"].append(".")
            mdrs["itemRgbs"].append(color)

        # find potential MDR regions
        potential_mdr_idxs = np.where(mdr_conf > 0)[0]
        potential_mdr_diff = np.diff(potential_mdr_idxs)
        potential_mdr_breaks = np.where(potential_mdr_diff != 1)[0] + 1 
        potential_mdrs = np.split(potential_mdr_idxs, potential_mdr_breaks)

        temp_entries = []
        
        for mdr in potential_mdrs:
            if len(mdr) == 0:
                continue
            min_idx, max_idx = np.min(mdr), np.max(mdr)
            start, end = methyl_starts[min_idx], methyl_starts[max_idx]+1
            mdr_scores = methyl_p_values[min_idx:max_idx+1]
            
            if (end-start) < self.min_size:
                continue 

            if any(mdr_conf[m]==2 for m in mdr):
                # process high confidence regions
                hc_idxs = np.where(mdr_conf[mdr] == 2)[0]
                hc_diff = np.diff(hc_idxs)
                hc_breaks = np.where(hc_diff != 1)[0] + 1 
                hc_mdrs = np.split(hc_idxs, hc_breaks)

                last_end = start
                for hc in hc_mdrs: 
                    min_hc_i, max_hc_i = mdr[np.min(hc)], mdr[np.max(hc)]
                    hc_start, hc_end = methyl_starts[min_hc_i], methyl_starts[max_hc_i]+1
                    hc_scores = methyl_p_values[min_hc_i:max_hc_i+1]

                    if last_end < hc_start:
                        #  create entry for low confidence region before high confidence region
                        lc_scores = methyl_p_values[min_idx:min_hc_i]
                        if len(lc_scores) > 0:
                            temp_entries.append((last_end, hc_start, f"low_confidence_{self.output_label}", lc_scores, self.lc_color))

                    # only add high confidence entries if they meet the minimum size requirement
                    if (hc_end-hc_start) >= self.min_size:
                        temp_entries.append((hc_start, hc_end, f"{self.output_label}", hc_scores, self.hc_color))
                    else:
                        # if high confidence region is too small, mark it as low confidence
                        temp_entries.append((hc_start, hc_end, f"low_confidence_{self.output_label}", hc_scores, self.lc_color))
                    last_end = hc_end

                # create entry for region after last high-confidence region if it exists
                if last_end < end:
                    lc_scores = methyl_p_values[max_hc_i:max_idx]
                    if len(lc_scores) > 0 and (end - last_end) >= self.min_size:
                        temp_entries.append((last_end, end, f"low_confidence_{self.output_label}", lc_scores, self.lc_color))

            else:
                # if none of the CpGs in the current run are high confidence
                temp_entries.append((start, end, f"low_confidence_{self.output_label}", mdr_scores, self.lc_color))


        # Merge nearby entries
        prev_entry = temp_entries[0]
        for cur_entry in temp_entries[1:]:
            if ((cur_entry[0] - prev_entry[1]) < self.merge_distance):
                # Entries are close enough to merge
                if ("low_confidence" in prev_entry[2]) and ("low_confidence" in cur_entry[2]):
                    # Merge two low confidence entries
                    prev_entry = (prev_entry[0], cur_entry[1], cur_entry[2], np.concatenate((prev_entry[3], cur_entry[3])), cur_entry[4])
                elif ("low_confidence" not in prev_entry[2]) and ("low_confidence" in cur_entry[2]):
                    # Keep high confidence entry separate, start new low confidence entry
                    create_entry_helper(prev_entry[0], prev_entry[1], prev_entry[2], prev_entry[3], prev_entry[4])
                    prev_entry = cur_entry
                elif ("low_confidence" in prev_entry[2]) and ("low_confidence" not in cur_entry[2]):
                    # End low confidence entry, start new high confidence entry
                    create_entry_helper(prev_entry[0], cur_entry[0], prev_entry[2], prev_entry[3], prev_entry[4])
                    prev_entry = cur_entry
                else: 
                    # Merge two high confidence entries
                    prev_entry = (prev_entry[0], cur_entry[1], cur_entry[2], np.concatenate((prev_entry[3], cur_entry[3])), cur_entry[4])
            else:
                # Entries are not close enough to merge
                create_entry_helper(prev_entry[0], prev_entry[1], prev_entry[2], prev_entry[3], prev_entry[4])
                prev_entry = cur_entry
        # Add the last entry
        create_entry_helper(prev_entry[0], prev_entry[1], prev_entry[2], prev_entry[3], prev_entry[4])

        return mdrs


    def priors_single_chromosome(self, chrom, methylation, regions):
        methylation_mannu = self.calculate_regional_stats(methylation)
        priors = self.find_priors(methylation_mannu)
        return ( chrom, priors, methylation_mannu)

    def priors_all_chromosomes(self, methylation_all_chroms, regions_all_chroms):
        priors_all_chroms = {}
        methylation_emissions_priors_all_chroms = {}

        with concurrent.futures.ProcessPoolExecutor(max_workers=self.threads) as executor:
            futures = {
                executor.submit(
                    self.priors_single_chromosome,
                    chrom, methylation_all_chroms[chrom], regions_all_chroms[chrom],
                ): chrom
                for chrom in methylation_all_chroms
            }

            for future in concurrent.futures.as_completed(futures):
                (
                    chrom,
                    priors,
                    methylation_emissions_priors,
                ) = future.result()

                priors_all_chroms[chrom] = priors
                methylation_emissions_priors_all_chroms[chrom] = methylation_emissions_priors

        return priors_all_chroms, methylation_emissions_priors_all_chroms


def main():
    argparser = argparse.ArgumentParser(
        description="Process bedMethyl and CenSat BED file to produce hmmCDR priors"
    )

    # required inputs
    argparser.add_argument("bedmethyl", type=str, help="Path to the bedmethyl file")
    argparser.add_argument("censat", type=str, help="Path to the censat BED file")
    argparser.add_argument("output", type=str, help="Path to the output BED file")

    # bed_parser arguments
    argparser.add_argument(
        "-m",
        "--mod_code",
        type=str,
        default="m",
        help='Modification code to filter bedMethyl file (default: "m")',
    )
    argparser.add_argument(
        "--methyl_bedgraph",
        action="store_true",
        default=False,
        help="Flag indicating if the input is a bedgraph. (default: False)",
    )
    argparser.add_argument(
        "--min_valid_cov",
        type=int,
        default=1,
        help="Minimum valid coverage to consider a methylation site (read from full modkit pileup files). (default: 1)",
    )
    argparser.add_argument(
        "-s",
        "--sat_type",
        type=str,
        default="active_hor",
        help='Comma-separated list of satellite types/names to filter CenSat bed file. (default: "active_hor")',
    )
    argparser.add_argument(
        "--edge_filter",
        type=int,
        default=0,
        help='Number of base pairs from edge of region to not include in the methylation calculations. (default: 0)',
    )
    argparser.add_argument(
        "--regions_prefiltered",
        action="store_true",
        default=False,
        help="Set flag if your annotations bed file is already subset to only the region you desire. (default: False)",
    )

    # calculate_matrices arguments
    argparser.add_argument(
        "--window_size",
        type=int,
        default=21,
        help="Number of CpGs to include in rolling window for CDR calculation. (default: 31)",
    )
    argparser.add_argument(
        "--step_size",
        type=int,
        default=1,
        help="Step size for rolling window calculations of CDRs. (default: 1)",
    )
    argparser.add_argument(
        "--high_conf_p",
        type=float,
        default=0.01,
        help="Cutoff for high confidence MDR p-value. (default: 0.0001)",
    )
    argparser.add_argument(
        "--low_conf_p",
        type=float,
        default=0.0,
        help="Cutoff for low confidence MDR p-value. (default: 0.05)",
    )
    argparser.add_argument(
        "--min_size",
        type=int,
        default=1000,
        help="Minimum size for a region to be labelled as an MDR. (default: 1000)",
    )
    argparser.add_argument(
        "--merge_distance",
        type=int,
        default=1000,
        help="Distance in bp to merge low confidence MDR annotations. (default: 1000)",
    )
    argparser.add_argument(
        "--enrichment",
        action="store_true",
        default=False,
        help="Enrichment flag. Pass in if you are looking for methylation enriched regions. (default: False)",
    )
    argparser.add_argument(
        "--threads",
        type=int,
        default=4,
        help='Number of threads to use for multithreading.',
    )
    argparser.add_argument(
        "--output_label",
        type=str,
        default="subCDR",
        help='Label to use for name column of priorCDR BED file. (default: "subCDR")',
    )

    args = argparser.parse_args()
    sat_types = [st.strip() for st in args.sat_type.split(",")]
    output_prefix = os.path.splitext(args.output)[0]

    parse_beds = bed_parser(
        mod_code=args.mod_code,
        methyl_bedgraph=args.methyl_bedgraph,
        min_valid_cov=args.min_valid_cov,
        sat_type=sat_types,
        edge_filter=args.edge_filter,
        regions_prefiltered=args.regions_prefiltered,
    )

    regions_dict, methylation_dict = parse_beds.process_files(
        methylation_path=args.bedmethyl,
        regions_path=args.censat,
    )

    mwcdr = calculate_matrices(
        window_size=args.window_size,
        step_size=args.step_size,
        high_conf_p=args.high_conf_p,
        low_conf_p=args.low_conf_p,
        min_size=args.min_size,
        merge_distance=args.merge_distance,
        enrichment=args.enrichment,
        threads=args.threads,
        output_label=args.output_label,
    )

    (
        cdrs_all_chroms,
        methylation_mannu_all_chroms,
    ) = mwcdr.priors_all_chromosomes(methylation_all_chroms=methylation_dict, regions_all_chroms=regions_dict)

    def generate_output_bed(all_chroms_dict, output_file, columns=["starts", "ends"]):
        all_lines = []
        for chrom in all_chroms_dict:
            chrom_data = all_chroms_dict[chrom]
            for i in range(len(chrom_data["starts"])):
                line = [chrom]
                for col in columns:
                    if col in chrom_data:
                        line.append(str(chrom_data[col][i])) 
                all_lines.append(line)
                
        all_lines = sorted(all_lines, key=lambda x: (x[0], int(x[1])))
        with open(output_file, 'w') as file:
            for line in all_lines: 
                file.write("\t".join(line) + "\n")

    generate_output_bed(cdrs_all_chroms, f"{output_prefix}_mwCDR.bed", columns=["starts", "ends", "names", "scores", "strands", "starts", "ends", "itemRgbs"])
    generate_output_bed(methylation_mannu_all_chroms, f"{output_prefix}_mannu.bedgraph", columns=["starts", "ends", "mannU_p_value"])

if __name__ == "__main__":
    main()