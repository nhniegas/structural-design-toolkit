==========================================================================
 logspiral_passive.py  -  USER GUIDE
 Passive earth pressure by the logarithmic-spiral method
 (soil with friction AND cohesion, with wall friction and wall adhesion)
 CE 264 Geotechnical Engineering - Terzaghi, Peck & Mesri, Article 32
==========================================================================

1. WHAT IT DOES
---------------
Computes the passive earth force (Pp) on a vertical wall retaining a
horizontal backfill.  It finds the critical wedge angle (the one that gives
the smallest Pp) automatically and reports:

   - the critical wedge angle (theta)
   - the passive force Pp, in kN per metre of wall length
   - the height z at which Pp acts above the base of the wall
   - the equivalent passive coefficient Kp

Pp is the sum of a frictional part (P_PI) and a cohesive part (P_PII) that
includes the effect of soil cohesion (c) and wall adhesion (ca).
Enter c = 0 and ca = 0 for a purely frictional (sand) backfill.


2. STARTING THE PROGRAM
-----------------------
The program is a single Python script.  It needs Python 3.8 or newer and
no other packages.  From the project folder, in a terminal:

   python geotech\logspiral_passive.py

A standalone LogSpiralPassive.exe (no Python needed) is not kept in the
repository.  It is built by the release workflow and attached to each
GitHub Release.  To build one yourself:

   python -m pip install pyinstaller
   python -m PyInstaller --onefile --console --name LogSpiralPassive geotech\logspiral_passive.py

If you use the .exe:
 * The first time, Windows may show "Windows protected your PC".
   Click "More info" and then "Run anyway".
 * Your antivirus may ask for confirmation.  This is a normal false alarm
   for small self-contained programs; allow it.


3. ENTERING DATA
----------------
The program asks for six values, one at a time.  The number in [brackets]
is the default.  Press ENTER to accept it, or type your own value and
press ENTER.

   Wall height H ................ metres         must be greater than 0
   Unit weight gamma ............ kN/m3          must be greater than 0
   Friction angle phi' .......... degrees         between 0 and 90
   Wall friction delta .......... degrees         0 up to phi'
   Cohesion c ................... kPa            0 or more
   Wall adhesion ca ............. kPa            0 or more

Use consistent units (metres, kN, kPa).  A comma is accepted as the
decimal mark (17,9 = 17.9).  If you type something invalid, the program
tells you why and asks again.

Then two questions (y = yes, n = no, ENTER = default):

   Show iteration table?        Shows each step of the search for the
                                critical angle.  Useful for checking.
   Show intermediate values?    Shows radii, areas, lever arms and forces
                                (handy for hand-checking the calculation).


4. READING THE RESULTS
----------------------
Example, using the default values
(H = 6.1, gamma = 17.9, phi = 36, delta = 20, c = 24, ca = 24):

   Wedge angle (theta) ........ 26.45932 deg      critical spiral angle
   Resultant (Pp) ............. 3398.05738 kN/m   total passive force
   Height of application (z) .. 2.30217 m         above the wall base
   Passive coefficient (Kp) ... 10.20347          Kp = Pp / (0.5*gamma*H^2)

Also shown:
   P_PI   frictional part of Pp
   P_PII  cohesive part (cohesion and adhesion)
   Check  area of the spiral sector computed two ways; the two numbers
          should be identical.

Note: the Kp shown is the equivalent coefficient for the whole force, not
the Rankine value.  The Rankine Kp appears under the intermediate values.

If delta = 0, the program may print a NOTE saying the critical surface
becomes the plane Rankine wedge (theta close to 0).  This is expected,
and the result is the Rankine passive value.


5. SAVING AND RUNNING MORE CASES
--------------------------------
 * "Save this result to a text file?"  Type y, then give a file name (or
   press ENTER for logspiral_result.txt).  The file is saved in the folder
   the program was started from, so start it from a folder you can write to.
 * "Run another case?"  Type y to enter new data.  The previous values
   become the new defaults, so you only retype what changes.
 * Type n to finish, then press ENTER to close the window.
 * Ctrl+C cancels at any time.


6. LIMITATIONS
--------------
 * Vertical wall and horizontal backfill surface only.
 * Passive pressure only; no surcharge, groundwater, or tension cracks.
 * Realistic strength values matter: cohesion and wall adhesion increase
   Pp a lot, so use conservative design values.
 * This is a teaching and analysis tool.  Check the results before using
   them for design.


7. TROUBLESHOOTING
------------------
 * "python" is not recognized:
   install Python from python.org and tick "Add python.exe to PATH".
 * Message "Minimum lies at the upper edge of the search range":
   check the inputs, especially very large c or ca compared with
   gamma x H.
 * The .exe window closes too fast / nothing appears:
   open Command Prompt, drag the .exe into it and press ENTER.
 * The .exe will not start (blocked):
   right-click the .exe -> Properties -> tick "Unblock" -> OK, or allow
   it in your antivirus.
==========================================================================
