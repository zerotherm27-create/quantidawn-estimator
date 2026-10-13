import fitz
from core.scale import effective_scale, implied_ratio, measure, paper_of

# Boxshall and Westgarth title blocks, as read off the sheets
print(effective_scale("SCALE: at A1 1:100", 1191, 842))
print(effective_scale("1:100", 1191, 842))
print(effective_scale("As indicated", 1191, 842))
print(effective_scale("1:100 at A3", 1191, 842))
for f in ("samples/boxshall-brighton/marked_up.pdf", "samples/82-westgarth/marked_up.pdf"):
    d = fitz.open(f)
    print(f.split("/")[1], {paper_of(p.rect.width, p.rect.height) for p in d}, {(round(p.rect.width), round(p.rect.height)) for p in d})
# round trip: 8.4 m at 1:200 -> points -> implied ratio
a, b = (100, 100), (100 + 8.4 * 1000 / 200 / (25.4 / 72), 100)
print(round(measure(a, b, 200), 3), round(implied_ratio(a, b, 8.4), 1))
