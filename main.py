"""Project entry point for the ETABS model automation.

    python main.py      define the standard parameters of an ETABS model:
                        materials, frame sections, load patterns, the UBC 97
                        response spectrum, load cases and load combinations
                        (etabs_api/model_setup.py)

The Excel workbooks do not use this file. Every Excel button runs its design
module directly:

    beam_column_designer_aci318.xlsm        design/beam_designer_aci318.py
                                            design/column_designer_aci318.py
                                            etabs_api/frame_tagger.py
    composite_column_designer_aiscDG06.xlsm design/composite_column_designer_aiscDG06.py
    wind_load_calculator_asce7.xlsm         design/wind_calculator_directional_asce7.py
"""

from etabs_api.model_setup import run_model_setup

if __name__ == "__main__":
    run_model_setup()
