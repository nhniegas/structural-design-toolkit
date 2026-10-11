"""The beam schedule drawings (DXF)."""

import os
import ezdxf
import pandas as pd
from design.criteria.code_config import CODE, AciCode
from design.concrete.beams.supports import (
    is_gravity_beam,
)


def export_beam_dxf(results: pd.DataFrame, output_dir: str) -> list[str]:
    """One girder and one gravity beam schedule DXF per story."""
    written = []
    if results.empty:
        return written
    if "SupportStatus" in results.columns:
        gravity = results["SupportStatus"].map(is_gravity_beam)
    else:
        gravity = pd.Series(False, index=results.index)
    for story in results["Story"].unique():
        on_story = results["Story"] == story
        for label, rows in (
            ("Girder", results[on_story & ~gravity]),
            ("Beam", results[on_story & gravity]),
        ):
            if rows.empty:
                continue
            path = os.path.join(output_dir, f"{story}_{label}_Schedule.dxf")
            generate_dxf_beam_schedule(story_name=f"{story} {label.upper()}S",
                                       df_story=rows, output_filepath=path)
            written.append(path)
    return written


def generate_dxf_beam_schedule(
    story_name: str,
    df_story: pd.DataFrame,
    output_filepath: str,
    code: AciCode = CODE,
):
    """Generates a structured CAD Beam Schedule DXF table for a single story using ezdxf."""
    seismic_cfg = code.beam_seismic
    detailing_cfg = code.beam_detailing
    # 1. Initialize DXF Document with SIMPLEX Text Style
    doc = ezdxf.new(dxfversion="R2018")
    if "SIMPLEX" not in doc.styles:
        doc.styles.new("SIMPLEX", dxfattribs={"font": "simplex.shx"})

    msp = doc.modelspace()

    # 2. Define Table Geometry (Column Widths & Row Heights in mm based on markup)
    col_widths = [
        15.0,  # 0: Beam Mark
        15.0,  # 1: Section B
        15.0,  # 2: Section H
        6.0,  # 3: LAYER
        15.0,  # 4: Left Support Top (Half of 30)
        15.0,  # 5: Left Support Bottom
        15.0,  # 6: Mid Support Top (Half of 30)
        15.0,  # 7: Mid Support Bottom
        15.0,  # 8: Right Support Top (Half of 30)
        15.0,  # 9: Right Support Bottom
        12.0,  # 10: Web Bars (UPDATED TO 12mm)
        40.0,  # 11: Stirrups
        15.0,  # 12: Remarks
    ]

    col_x = [0.0]
    for w in col_widths:
        col_x.append(col_x[-1] + w)

    header_h = 6.0  # Height per header row (2 rows = 12mm total)
    subrow_h = 4.5  # Height per layer sub-row (3 sub-rows = 13.5mm total per beam)

    def add_cell_text(
        text,
        x_left,
        y_top,
        width,
        height,
        font_size=2.0,
        width_factor=0.75,
        style="SIMPLEX",
        rotation=0,
    ):
        """Custom text adder that safely splits \n into stacked CAD text entities."""
        lines = str(text).split("\n")
        num_lines = len(lines)

        # Calculate vertical shift to keep the multi-line block centered
        line_spacing = font_size * 1.5
        total_text_height = (num_lines - 1) * line_spacing

        cx = x_left + width / 2.0
        start_cy = (y_top - height / 2.0) + (total_text_height / 2.0)

        for idx, line_text in enumerate(lines):
            cy = start_cy - (idx * line_spacing)
            text_ent = msp.add_text(
                line_text,
                dxfattribs={
                    "style": style,
                    "height": font_size,
                    "width": width_factor,  # Applies the 0.75 width factor
                    "color": 7,
                    "rotation": rotation,
                },
            )
            # If text is rotated (like the LAYER column), swap middle-center alignment handling
            text_ent.set_placement(
                (cx, cy), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER
            )

    def draw_box(x1, y1, x2, y2, lineweight=25):
        """Draw a closed rectangular polyline around a schedule cell or row."""
        pts = [(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)]
        msp.add_lwpolyline(pts, dxfattribs={"color": 7, "lineweight": lineweight})

    # 3. Draw Main Table Headers
    y_curr = 0.0

    # Header Outer Box
    draw_box(col_x[0], y_curr, col_x[-1], y_curr - 2 * header_h, lineweight=35)

    # Header Column Titles & Dividers
    add_cell_text("Beam\nMark", col_x[0], y_curr, col_widths[0], 2 * header_h)
    msp.add_line(
        (col_x[1], y_curr), (col_x[1], y_curr - 2 * header_h), dxfattribs={"color": 7}
    )

    add_cell_text("Section", col_x[1], y_curr, col_widths[1] + col_widths[2], header_h)
    add_cell_text("B (mm)", col_x[1], y_curr - header_h, col_widths[1], header_h)
    add_cell_text("H (mm)", col_x[2], y_curr - header_h, col_widths[2], header_h)
    msp.add_line(
        (col_x[1], y_curr - header_h),
        (col_x[3], y_curr - header_h),
        dxfattribs={"color": 7},
    )
    msp.add_line(
        (col_x[2], y_curr - header_h),
        (col_x[2], y_curr - 2 * header_h),
        dxfattribs={"color": 7},
    )
    msp.add_line(
        (col_x[3], y_curr), (col_x[3], y_curr - 2 * header_h), dxfattribs={"color": 7}
    )

    # Rotated the LAYER text 90 degrees to fit the 6mm column
    add_cell_text("LAYER", col_x[3], y_curr, col_widths[3], 2 * header_h, rotation=90)

    msp.add_line(
        (col_x[4], y_curr), (col_x[4], y_curr - 2 * header_h), dxfattribs={"color": 7}
    )

    add_cell_text(
        "Left Support", col_x[4], y_curr, col_widths[4] + col_widths[5], header_h
    )
    add_cell_text("Top", col_x[4], y_curr - header_h, col_widths[4], header_h)
    add_cell_text("Bottom", col_x[5], y_curr - header_h, col_widths[5], header_h)
    msp.add_line(
        (col_x[4], y_curr - header_h),
        (col_x[6], y_curr - header_h),
        dxfattribs={"color": 7},
    )
    msp.add_line(
        (col_x[5], y_curr - header_h),
        (col_x[5], y_curr - 2 * header_h),
        dxfattribs={"color": 7},
    )
    msp.add_line(
        (col_x[6], y_curr), (col_x[6], y_curr - 2 * header_h), dxfattribs={"color": 7}
    )

    add_cell_text(
        "Mid Support", col_x[6], y_curr, col_widths[6] + col_widths[7], header_h
    )
    add_cell_text("Top", col_x[6], y_curr - header_h, col_widths[6], header_h)
    add_cell_text("Bottom", col_x[7], y_curr - header_h, col_widths[7], header_h)
    msp.add_line(
        (col_x[6], y_curr - header_h),
        (col_x[8], y_curr - header_h),
        dxfattribs={"color": 7},
    )
    msp.add_line(
        (col_x[7], y_curr - header_h),
        (col_x[7], y_curr - 2 * header_h),
        dxfattribs={"color": 7},
    )
    msp.add_line(
        (col_x[8], y_curr), (col_x[8], y_curr - 2 * header_h), dxfattribs={"color": 7}
    )

    add_cell_text(
        "Right Support", col_x[8], y_curr, col_widths[8] + col_widths[9], header_h
    )
    add_cell_text("Top", col_x[8], y_curr - header_h, col_widths[8], header_h)
    add_cell_text("Bottom", col_x[9], y_curr - header_h, col_widths[9], header_h)
    msp.add_line(
        (col_x[8], y_curr - header_h),
        (col_x[10], y_curr - header_h),
        dxfattribs={"color": 7},
    )
    msp.add_line(
        (col_x[9], y_curr - header_h),
        (col_x[9], y_curr - 2 * header_h),
        dxfattribs={"color": 7},
    )
    msp.add_line(
        (col_x[10], y_curr), (col_x[10], y_curr - 2 * header_h), dxfattribs={"color": 7}
    )

    add_cell_text("Web\nBars", col_x[10], y_curr, col_widths[10], 2 * header_h)
    msp.add_line(
        (col_x[11], y_curr), (col_x[11], y_curr - 2 * header_h), dxfattribs={"color": 7}
    )

    add_cell_text("Stirrups", col_x[11], y_curr, col_widths[11], 2 * header_h)
    msp.add_line(
        (col_x[12], y_curr), (col_x[12], y_curr - 2 * header_h), dxfattribs={"color": 7}
    )

    add_cell_text("Remarks", col_x[12], y_curr, col_widths[12], 2 * header_h)

    y_curr -= 2 * header_h

    # 4. Populate Data Rows per Beam
    if not df_story.empty:
        unique_beams = df_story["UniqueName"].unique()

        for u_name in unique_beams:
            df_beam = df_story[df_story["UniqueName"] == u_name]
            top_row = df_beam[df_beam["Face"] == "TOP"].iloc[0]
            bot_row = df_beam[df_beam["Face"] == "BOTTOM"].iloc[0]

            beam_h = 3 * subrow_h
            y_beam_start = y_curr

            add_cell_text(u_name, col_x[0], y_beam_start, col_widths[0], beam_h)

            b_val = int(top_row.get("Width", top_row.get("b", 300)))
            h_val = int(top_row.get("Depth", top_row.get("Height", 500)))
            add_cell_text(b_val, col_x[1], y_beam_start, col_widths[1], beam_h)
            add_cell_text(h_val, col_x[2], y_beam_start, col_widths[2], beam_h)

            n_side = int(top_row.get("n_side_per_face_gov", 0))
            dw = int(top_row.get("dw", top_row.get("dm", 12)))
            web_bars_str = f"{2 * n_side} - {dw}Ø" if n_side > 0 else "-"
            add_cell_text(web_bars_str, col_x[10], y_beam_start, col_widths[10], beam_h)

            # Stirrups Detailing
            ds = int(top_row.get("ds", top_row.get("d_stirrup", 10)))
            sp_2h = float(top_row.get("Spacing_2H", 100))
            sp_mid = float(top_row.get("Spacing_Mid", 200))
            first_offset = detailing_cfg.first_stirrup_offset
            two_h = seismic_cfg.hoop_zone_depth_factor * h_val
            n_2h = int(two_h / sp_2h) if sp_2h > 0 else 0

            # Use \n to split the long string onto two lines
            stirrups_str = (
                f"{ds}Ø - 1@{first_offset:g}mm, {n_2h}@{int(sp_2h)}mm;"
                f"\nREST @{int(sp_mid)}mm o.c."

            )
            add_cell_text(stirrups_str, col_x[11], y_beam_start, col_widths[11], beam_h)

            legs = int(top_row.get("Stirrup_Legs", 2))
            type_num = max(1, legs - 1)
            remarks_str = f"TYPE - {type_num}"
            add_cell_text(remarks_str, col_x[12], y_beam_start, col_widths[12], beam_h)

            dm = int(top_row.get("dm", top_row.get("d_main", 25)))

            for layer_idx in [1, 2, 3]:
                y_sub_top = y_beam_start - (layer_idx - 1) * subrow_h

                add_cell_text(layer_idx, col_x[3], y_sub_top, col_widths[3], subrow_h)

                n_top_left = int(top_row.get(f"n_left_L{layer_idx}", 0))
                n_bot_left = int(bot_row.get(f"n_left_L{layer_idx}", 0))

                n_top_mid = int(top_row.get(f"n_mid_L{layer_idx}", 0))
                n_bot_mid = int(bot_row.get(f"n_mid_L{layer_idx}", 0))

                n_top_right = int(top_row.get(f"n_right_L{layer_idx}", 0))
                n_bot_right = int(bot_row.get(f"n_right_L{layer_idx}", 0))

                str_top_left = f"{n_top_left} - {dm}Ø" if n_top_left > 0 else "-"
                str_bot_left = f"{n_bot_left} - {dm}Ø" if n_bot_left > 0 else "-"

                str_top_mid = f"{n_top_mid} - {dm}Ø" if n_top_mid > 0 else "-"
                str_bot_mid = f"{n_bot_mid} - {dm}Ø" if n_bot_mid > 0 else "-"

                str_top_right = f"{n_top_right} - {dm}Ø" if n_top_right > 0 else "-"
                str_bot_right = f"{n_bot_right} - {dm}Ø" if n_bot_right > 0 else "-"

                add_cell_text(
                    str_top_left, col_x[4], y_sub_top, col_widths[4], subrow_h
                )
                add_cell_text(
                    str_bot_left, col_x[5], y_sub_top, col_widths[5], subrow_h
                )
                add_cell_text(str_top_mid, col_x[6], y_sub_top, col_widths[6], subrow_h)
                add_cell_text(str_bot_mid, col_x[7], y_sub_top, col_widths[7], subrow_h)
                add_cell_text(
                    str_top_right, col_x[8], y_sub_top, col_widths[8], subrow_h
                )
                add_cell_text(
                    str_bot_right, col_x[9], y_sub_top, col_widths[9], subrow_h
                )

                if layer_idx < 3:
                    y_line = y_sub_top - subrow_h
                    msp.add_line(
                        (col_x[3], y_line),
                        (col_x[10], y_line),
                        dxfattribs={"color": 7},
                    )

            y_beam_end = y_beam_start - beam_h
            draw_box(col_x[0], y_beam_start, col_x[-1], y_beam_end, lineweight=25)

            for idx in range(1, len(col_x) - 1):
                msp.add_line(
                    (col_x[idx], y_beam_start),
                    (col_x[idx], y_beam_end),
                    dxfattribs={"color": 7},
                )

            y_curr = y_beam_end

    doc.saveas(output_filepath)
