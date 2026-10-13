import cv2
import fitz  # PyMuPDF
import numpy as np


class VisionError(Exception):
    """Raised for problems opening a PDF page or running template matching."""


class VisionTakeoff:
    """Symbol-counting via OpenCV template matching.

    Known limitation, unchanged from the original spec: template matching
    needs a close pixel match in scale, rotation, and rendering style. A
    door symbol rotated 90 degrees, drawn by a different CAD tool, or
    printed at a different sheet scale will likely be missed. This is fine
    as a proof-of-concept for a single, consistent plan set, but it is not
    a substitute for a trained detection model if you plan to run this
    across plans from many different architects or sources. Flagging this
    here so it is not forgotten once the tool is in daily use.
    """

    @staticmethod
    def pdf_page_to_image(pdf_path: str, page_num: int = 0):
        try:
            doc = fitz.open(pdf_path)
            if page_num >= doc.page_count:
                raise VisionError(
                    f"Page {page_num} does not exist - this PDF has {doc.page_count} page(s)."
                )
            page = doc.load_page(page_num)
            pix = page.get_pixmap(dpi=150)
            img_data = np.frombuffer(pix.samples, dtype=np.uint8).reshape((pix.h, pix.w, pix.n))
            if pix.n == 3:
                img_bgr = cv2.cvtColor(img_data, cv2.COLOR_RGB2BGR)
            else:
                img_bgr = cv2.cvtColor(img_data, cv2.COLOR_GRAY2BGR)
            return img_bgr
        except VisionError:
            raise
        except Exception as exc:
            raise VisionError(f"Could not open page {page_num} of this PDF: {exc}") from exc

    @staticmethod
    def count_symbols(blueprint_img, template_img, threshold: float = 0.75):
        """Template-matches a cropped symbol image against a blueprint page.
        Returns the annotated image and the count of matches found."""
        if template_img is None or blueprint_img is None:
            raise VisionError("Missing image data - the blueprint or template failed to load.")

        gray_blueprint = cv2.cvtColor(blueprint_img, cv2.COLOR_BGR2GRAY)
        gray_template = cv2.cvtColor(template_img, cv2.COLOR_BGR2GRAY)
        h, w = gray_template.shape

        if h > gray_blueprint.shape[0] or w > gray_blueprint.shape[1]:
            raise VisionError(
                "The template image is larger than the blueprint page - "
                "crop a smaller symbol to search for."
            )

        res = cv2.matchTemplate(gray_blueprint, gray_template, cv2.TM_CCOEFF_NORMED)
        loc = np.where(res >= threshold)
        points = list(zip(*loc[::-1]))

        detections = []
        for pt in points:
            overlaps_existing = any(
                abs(pt[0] - d[0]) < w // 2 and abs(pt[1] - d[1]) < h // 2 for d in detections
            )
            if not overlaps_existing:
                detections.append(pt)
                cv2.rectangle(blueprint_img, pt, (pt[0] + w, pt[1] + h), (0, 0, 255), 2)
                cv2.circle(blueprint_img, (pt[0] + w // 2, pt[1] + h // 2), 5, (0, 255, 0), -1)

        return blueprint_img, len(detections)
