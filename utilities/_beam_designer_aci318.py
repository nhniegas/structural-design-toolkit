import pandas as pd
import xlwings as xw

# Dictionary containing ACI 318 provision snippets
ACI_PROVISIONS = {
    "ACI 318-14 Section 9.5.2": (
        "9.5.2 Moment\n"
        "----------------------------------------\n"
        "9.5.2.1 If Pu < 0.10f'cAg, Mn shall be calculated  in accordance with 22.3.\n"
        "9.5.2.1 If Pu >= 0.10f'cAg, Mn shall be calculated  in accordance with 22.4."
    ),
    "ACI 318-14 Section 9.6.3.1": (
        "9.6.3.1 Minimum Shear Reinforcement\n"
        "----------------------------------------\n"
        "A minimum area of shear reinforcement (Av,min) shall be provided "
        "where Vu > 0.5 * phi * Vc."
    ),
    "ACI 318-14 Section 9.7.2.3": (
        "9.7.2.3 Skin Reinforcement\n"
        "----------------------------------------\n"
        "For nonprestressed and Clas C Presstressed beams, with h exceeding 900mm\n"
        "longitudinal skin reinforcement shall be uniformly distributed on both sides\n"
        "of the beam for a distance h/2 from the tension face. Spacing of skin reinforcemen\n"
        "shall not exceed h/2 for the tension face."
    ),
    "ACI 318-14 Section 9.7.3.8.1": (
        "9.7.3.8.1  Termination of Reinforcement\n"
        "----------------------------------------\n"
        "At simple supports, at least one-third of the maximum positive reinforcement\n"
        "shall extend along the beam bottom into the support at least 150mm, except\n"
        "for precast beams where such reinforcement shall extend into the center of\n"
        "length."
    ),
    "ACI 318-14 Section 9.7.3.8.2": (
        "9.7.3.8.2 Termination of Reinforcement\n"
        "----------------------------------------\n"
        "At other supports, at least one-fourth of the maximum positive reinforcement\n"
        "shall extend along the beam bottom into the support at least 150mm, and\n"
        "if the beam is part of the lateral-load-resisting system, shall be acnchored\n"
        "to develop fy at the face of the support."
    ),    
    "ACI 318-14 Section 9.7.5.1": (
        "9.7.5.1 Longitudinal Torsional Reinforcement\n"
        "----------------------------------------\n"
        "If torsional reinforcement is required, longitudinal reinforcement shall be\n"
        "distributed around the perimeter of closed stirrups that satisfy 25.7.1.6\n"
        "or hoops with spacing not greater than 300 mm\n" 
    ),
    "ACI 318-14 Section 9.7.6.2.2": (
        "9.7.6.2.2 Shear\n"
        "----------------------------------------\n"
        "Maximum Spacing of shear reinforcement shall be in accordance wit Table 9.7.6.2.2"
    ),
    "ACI 318-14 Section 9.7.7.2": (
        "9.7.7.2 Structural Integrity Reinforcement\n"
        "----------------------------------------\n"
        "(a) At least one-quarter of the maximum positive reinforcement, but not less than two bars\n"
        "or strands, shall be continuous\n"
        "(b) Longitudinal reinforcement shall be enclosed by closed stirrups in accordance with 25.7.1.6\n"
        "or hoops along the clear span of the beam."
    ),
    "ACI 318-14 Section 18.6.3.1": (
        "18.6.3.1 Longitudinal Reinforcement\n"
        "----------------------------------------\n"
        "Beams shall have at least two continuous bars at top and bottom faces. At any section, for top as\n"
        "as well as for bottom reinforcement, the amount of reinforcement shall be at least that required by\n"
        "9.6.1.2 and the reinforcement ratio shall not exceed 0.025\n"
    ),
    "ACI 318-14 Section 18.6.3.2": (
        "18.6.3.2 Longitudinal Reinforcement\n"
        "----------------------------------------\n"
        "Positive moment strength at joint face shall be at least one-half the negative moment strength provided\n"
        "at the face of the joint. Both the negative and the postive moment strength at any section along the member\n"
        "length shall be at least one-fourth the maximum moment strength provided at face of either joint\n"
        ),
    "ACI 318-14 Section 18.6.4.1": (
        "18.6.4.1 Transverse Reinforcement\n"
        "----------------------------------------\n"
        "Hoops shall be provided in the following regions of a beam:\n"
        "(a) Over a length equal to twice the beam depth measure from the face of the supporting column toward\n"
        "midspan, at both ends of the beam\n"
        "(b) Over a length equal to twice the beam depth on both sides of a section where flexural yielding is\n"
        "to occur as a result of lateral displacements beyond the elastic range of behavior\n"
    ),
    "ACI 318-14 Section 18.6.4.4": (
        "18.6.4.4 Transverse Reinforcement\n"
        "----------------------------------------\n"
        "The first hoop shall be located not more than 50 mm from the face of a supporting column.\n"
        "Spacing of the hoops shall not exceed the least of (a) through c.\n"
        
        "(a) d/4 \n"
        "(b) Six times the diameter of the smallest primary flexural reinforcing bars excluding \n"
        "longitudinal skin reinforcement required by 9.7.23\n"
        "(c) 150 mm\n"

    ),
    "ACI 318-14 Section 18.6.4.6": (
        "18.6.4.6 Transverse Reinforcement\n"
        "----------------------------------------\n"
        "Where hoops are not required, stirrups with seismic hooks at book ends shall be spaced at a\n"
        "distance not more than d/2 throughout the length of the beam.\n"
    ),
    "ACI 318-14 Section 18.6.5.1": (
        "18.6.5.1 Shear Strength\n"
        "----------------------------------------\n"
        "Design forces - The design shear force Ve shall be calculated from considerations of the forces\n"
        "on the portion of the beam between faces of the joints. It shall be assummed that moments of opposite\n"
        "sign corresponding to probable flexural strength, Mpr, act at the joint faces and the beam is loaded\n"
        "with the factored tributary gravity load along its span.\n"
    ),
        "ACI 318-14 Section 18.6.5.2": (
        "18.6.5.2 Shear Strength\n"
        "----------------------------------------\n"
        "Transverse reinforcement - Transverse reinforcement over the lengths identified in 18.6.4.1 shall be designed\n"
        "to resist shear assuming Vc = 0 when both (a) and (b) occur:\n"
        "(a) The earthquake-induced shear force calculated in accordance with 18.6.5.1 represents at least one-half of the \n"
        "maximum requried shear strengths within those lengths\n"
        "(b) The factored axial compressive force Pu including earthquake effects is less than Agf'c/20. \n"
    ),
}

def attach_provision_notes(
    sheet_name: str = "OVERWRITES", target_range: str = "G1:G50"
):
    """Attaches ACI code provision hover notes and auto-sizes each note box to fit the full snippet."""
    try:
        wb = xw.Book.caller()
    except Exception:
        wb = xw.books.active

    sheet = wb.sheets[sheet_name]

    for cell in sheet.range(target_range):
        if cell.value is None:
            continue

        cell_text = str(cell.value).strip()

        for key, snippet in ACI_PROVISIONS.items():
            if key.lower() in cell_text.lower():
                try:
                    cell.api.ClearComments()
                except Exception:
                    pass

                # Add comment box
                cell.api.AddComment(snippet)

                if cell.api.Comment is not None:
                    # Auto-fit box dimensions to text length
                    cell.api.Comment.Shape.TextFrame.AutoSize = True
                    # Keep hidden until mouse hovers
                    cell.api.Comment.Visible = False
                break
            
def identify_cantilever_beams(
    frame_df: pd.DataFrame, conn_df: pd.DataFrame
) -> pd.DataFrame:
    """Identifies beam support conditions using both Column and Wall joint connectivity."""
    conn_df.columns = [str(c).strip() for c in conn_df.columns]

    # 1. Extract support joints across all Column and Wall members
    support_rows = conn_df[conn_df["DesignType"].isin(["Column", "Wall"])]

    # Collect all possible point joint column names across columns and walls
    pt_cols = [
        c
        for c in [
            "UniquePtI",
            "UniquePtJ",
            "UniquePt1",
            "UniquePt2",
            "UniquePt3",
            "UniquePt4",
        ]
        if c in support_rows.columns
    ]

    support_joints = set()
    for col in pt_cols:
        support_joints.update(support_rows[col].dropna().tolist())

    # 2. Filter connectivity data for Beams only
    beam_conn = conn_df[conn_df["DesignType"] == "Beam"].copy()

    # 3. Check connectivity at both end joints against vertical support joints (Column/Wall)
    beam_conn["Has_Support_PtI"] = beam_conn["UniquePtI"].isin(support_joints)
    beam_conn["Has_Support_PtJ"] = beam_conn["UniquePtJ"].isin(support_joints)

    # 4. Determine Support Status
    def get_status(row):
        sup_count = sum([row["Has_Support_PtI"], row["Has_Support_PtJ"]])
        if sup_count == 2:
            return "Supported Both Ends"
        elif sup_count == 1:
            cant_pt = "PtI" if not row["Has_Support_PtI"] else "PtJ"
            return f"Cantilever (Free at {cant_pt})"
        else:
            return "Beam-Framed / Floating"

    beam_conn["SupportStatus"] = beam_conn.apply(get_status, axis=1)

    return beam_conn[["UniqueName", "SupportStatus"]].reset_index(drop=True)