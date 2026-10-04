# Request 03: last follow-ups

Thank you for `2026-10-05-request-02/`. It settled A, B, D, E and G. The
4D28 in the 500x500 girders is traced: the beam design keeps the clear space
between main bars at 150 mm or less (`get_min_bars_for_150mm_spacing`), which
gives 4 bars on a 500 mm beam whatever the moment. Two points remain. Same
rules as before: scratch copies, the dialog log at the top of each file. Save
under `test-results/<date>-request-03/` with `README.md` and `notes.txt`.

## C. Why GF-C1 has Ve about 9,000 kN

Ve x lu for GF-C1 is about 4,600 kN-m, more than the probable moments of a
600x600 column can give. The workbook has no Mpr columns, but the PDF
calculation report of `sdt columns` should show the capacity shear working.

1. Run `sdt columns` on a fresh copy with the defaults (foundation level
   checked), and keep the PDF report.
2. `C3_gf_c1_report.txt`: the text of the PDF pages for **GF-C1**, and for
   comparison **GF-C2**, covering the column shear / capacity design part:
   Mpr at each end, the axial load used, any beam moment limit, lu, and Ve.
   Copying the text (for example with `pdftotext -layout`, which comes with
   MiKTeX, or by copy and paste) is enough.
3. In notes: for each of the two columns, the axial load P that went with the
   largest Ve, and whether it is compression or tension (uplift at the
   corner?).

## F. Earth cover on a 250 mm beam (expected crash)

On a fresh copy, assign a 250 mm wide beam section to one GF tie beam (define
`B_250X400` with the same concrete if needed). Then run `sdt beams` answering
**Earth cover: Yes**, choosing GF; all else defaults. Save as
`F2_beams_250_earth.txt`. Expected: the whole run stops with
`ValueError: SECTION DETAILING ERROR ... fits max 1 main bars per layer, but requires 2 stirrup legs`.
Report whether it does, and whether any output files were written.
