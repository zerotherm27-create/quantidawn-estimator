# QuantiDawn AI Estimator

Run the web app:

    ./.venv/bin/python server.py      # then open http://127.0.0.1:5050

What it does today
- Opens the two sample projects (Boxshall, Westgarth): runs the real rules pipeline over the legend
  entries read from their marked-up plans, shows the sheets, and lets you review, measure, verify and export.
- Accepts PDF uploads: renders the pages and detects each sheet's scale. Takeoff lines for uploads need AI
  measuring, which is not connected yet, so uploaded projects start empty.
- Exports a takeoff workbook, or fills a client's own Excel template (formulas are never overwritten).
- "Demo AI check" shows planted disagreements to illustrate the cross-check. It is not a real AI run.

Optional pricing (off by default, per project)
- The Pricing screen (the $ icon) switches pricing on for a project when the client wants costs. Off means
  quantities only: no Rate/Cost columns and none in the export.
- Load a CSV/Excel price list (description, unit, rate, optional category; see samples/pricing/ for the format -
  the rates in that file are EXAMPLES, not real). A line only takes a rate of the same kind of work and unit.
  Confident matches apply, plausible ones are suggested and wait for a person to accept, the rest stay blank.
  Labour: the price list may also have labour_hours (hours per unit) and labour_rate ($/hr) columns; a default
  labour rate can be set on the Pricing screen. Hours are never costed without a rate. In a client's BOQ template
  the hours (a live formula off the row's quantity) and labour rate go into its Labour columns.
  Rates can also be typed per line. The export adds Rate and Cost columns and a Pricing summary sheet
  (subtotal, contingency, margin, GST). Rates, labour and a rate-source note are written into a client's own BOQ template too (only typed cells, never formulas).

Brand
- The official logo files are copied from quantidawn.com into `web/brand/` (logo-full.png for light backgrounds,
  logo-white.png for dark, plus the favicons). They show on the upload screen, the sign-in page and the client
  download page, and the Q mark is in the app's side rail. Swap the files there if the logo is updated.

Pop-out plan window
- "Pop out" (above the plan) opens the plan in its own window, handy for a second screen. It is the same page
  with ?plan=1. The two windows share one document over a BroadcastChannel: measure, drag corners, confirm or
  enter a scale, or pick the line to measure in the pop-out, and the table and totals in the main window update
  at once; selecting a line in the main window moves the pop-out to it. Each window keeps its own pan and zoom.
  Closing the pop-out (or "Bring back to main window") puts the plan back. Last change wins if both edit at once.

Sharp zoom (applies to every project, sample or uploaded)
- Pages are shown from a 2 px/pt base render. Zoom past that and the viewer loads a crop of the visible area
  from `GET /tile/<project>/<page>?x&y&w&h&z`, rendered at the PDF's own embedded resolution (`max_z`, stored
  per page; vector pages cap at 8). Tiles are cached in `data/tiles/`.
- A file can never be sharper than its source: the plan HUD says "at the PDF's own resolution" when you zoom
  past it. Low-dpi PDFs (for example 200 dpi scans) need a better export from the architect or engineer.
- Do not put `will-change: transform` on the plan viewport: it makes browsers stretch a low-res texture.

Tests: `PYTHONPATH=. ./.venv/bin/python -m tests.test_server` (also test_calc, test_reconcile, test_scale, test_template_fill, test_price_matcher, test_pricing_export, test_pricing_totals, test_template_rates, eval_takeoff).
The earlier static prototype is kept in design/.
