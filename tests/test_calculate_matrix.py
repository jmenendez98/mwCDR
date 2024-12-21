import pytest
from hmmCDR.bed_parser import bed_parser
from hmmCDR.calculate_matrices import calculate_matrices
import os

class TestMatrix:
    @pytest.fixture
    def test_data(self):
        """Fixture to set up test data and parser"""
        test_data_dir = os.path.join("tests", "data")
        
        parser = bed_parser(
            mod_code="m",
            bedgraph=False,
            min_valid_cov=10,
            sat_type=['active_hor'],
            pre_subset_censat=False,
        )
        
        bedmethyl_test = os.path.join(test_data_dir, "bedmethyl_test.bed")
        censat_test = os.path.join(test_data_dir, "censat_test.bed")

        return parser.process_files(
            bedmethyl_path=bedmethyl_test,
            censat_path=censat_test,
        )
            
    
    @pytest.fixture
    def matrix_calculator(self):
        """Fixture for matrix calculator"""
        return calculate_matrices(
            window_size=1190,
            step_size=1190,
            min_prior_size=8330,
            enrichment=False,
            percentile_emissions=False,
            w=0.0,
            x=33.0,
            y=66.0,
            z=100.0,
            output_label="CDR",
        )

    def test_making_matrices(self, test_data, matrix_calculator):
        """Test making matrices"""
        (
            priors_chrom_dict,
            windowmean_chrom_dict,
            labelled_methylation_chrom_dict,
            emission_matrix_chrom_dict,
            transition_matrix_chrom_dict,
        ) = matrix_calculator.priors_all_chromosomes(
            methylation_chrom_dict=test_data[0],
            regions_chrom_dict=test_data[1],
            prior_percentile=False,
            prior_threshold=20,
        )

        # Changed from .values to proper dictionary access
        assert isinstance(priors_chrom_dict, dict)
        assert isinstance(windowmean_chrom_dict, dict)
        assert isinstance(labelled_methylation_chrom_dict, dict)
        assert isinstance(emission_matrix_chrom_dict, dict)
        assert isinstance(transition_matrix_chrom_dict, dict)
        assert len(priors_chrom_dict) == 1
        assert len(windowmean_chrom_dict) == 1
        assert len(labelled_methylation_chrom_dict) == 1
        assert len(emission_matrix_chrom_dict) == 1
        assert len(transition_matrix_chrom_dict) == 1

        # Add more specific assertions about the matrices
        for chrom in emission_matrix_chrom_dict:
            assert emission_matrix_chrom_dict[chrom].shape == (2, 4)
            # Add assertions about matrix shape or content if known
        
        for chrom in transition_matrix_chrom_dict:
            assert transition_matrix_chrom_dict[chrom].shape == (2, 2)
            # Add assertions about matrix shape or content if known
            