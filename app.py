import os

import cv2
import numpy as np
import streamlit as st

from core.excel_gen import ExcelGenerator
from core.llm_parser import LLMParser, LLMParserError
from core.render import page_info, render_crop, view_window
from core.vision import VisionError, VisionTakeoff

st.set_page_config(page_title="AI Estimator Control Panel", layout="wide")


@st.cache_data(show_spinner=False, max_entries=64)
def cached_crop(path, mtime, page, x, y, w, h, z):
    return render_crop(path, page, x, y, w, h, z)


def sharp_page_viewer(pdf_path, page_index):
    """Zoom and pan a plan page at the PDF's own resolution (never sharper than the file allows)."""
    try:
        pw, ph, cap, n_pages = page_info(pdf_path, page_index)
    except Exception as exc:
        st.error(f"Could not open this page: {exc}")
        return
    c1, c2, c3 = st.columns(3)
    zoom = c1.slider("Zoom", 1.0, 12.0, 1.0, 0.5, key=f"zoom_{page_index}")
    pan_x = c2.slider("Left to right", 0, 100, 50, disabled=zoom == 1.0, key=f"panx_{page_index}")
    pan_y = c3.slider("Top to bottom", 0, 100, 50, disabled=zoom == 1.0, key=f"pany_{page_index}")
    x, y, w, h = view_window(pw, ph, zoom, pan_x, pan_y)
    png, z_used, capped = cached_crop(pdf_path, os.path.getmtime(pdf_path), page_index,
                                      round(x, 1), round(y, 1), round(w, 1), round(h, 1), 1600 / w)
    st.image(png, use_container_width=True, caption=f"Page {page_index} of {n_pages - 1}, zoom {zoom:g}x")
    if capped:
        st.warning(
            f"This PDF's own resolution is about {cap:.1f} pixels per point, so zooming further adds no detail. "
            "Ask the architect or engineer for a vector (CAD) export for a sharper plan."
        )
    else:
        st.caption(f"Sharp: rendered at {z_used:.1f} px per point (this file allows up to {cap:.1f}).")
st.title("AI-Assisted Takeoff & Estimation Workspace")

# Streamlit re-runs this whole script on every interaction. Without storing
# results in session_state, the scope analysis computed in one button click
# would be gone by the time the Export button is pressed - that was a real
# bug in the original version, not just a style issue.
if "parsed_data" not in st.session_state:
    st.session_state.parsed_data = None
if "counted_items" not in st.session_state:
    st.session_state.counted_items = {}

region = st.sidebar.selectbox("Target Bidding Region", ["US", "AU", "SG"])
uploaded_pdf = st.sidebar.file_uploader("Upload Blueprint Plan (PDF)", type=["pdf"])

col1, col2 = st.columns(2)

if uploaded_pdf:
    os.makedirs("temp", exist_ok=True)
    pdf_path = os.path.join("temp", uploaded_pdf.name)
    with open(pdf_path, "wb") as f:
        f.write(uploaded_pdf.getbuffer())

    with col1:
        st.subheader("Document Scope & Materials")
        if st.button("Extract Scope & Match Standards"):
            with st.spinner("Reading the document and analyzing scope..."):
                try:
                    parser = LLMParser()
                    raw_text = parser.extract_text_from_pdf(pdf_path)
                    result = parser.parse_scope_and_materials(raw_text, region)
                    st.session_state.parsed_data = result
                    st.success(f"Extracted {len(result['materials'])} material items.")
                except LLMParserError as exc:
                    st.error(str(exc))

        if st.session_state.parsed_data:
            data = st.session_state.parsed_data
            if data.get("scope_summary"):
                st.markdown(f"**Scope summary:** {data['scope_summary']}")
            if data.get("materials"):
                st.markdown("**Materials extracted:**")
                for item in data["materials"]:
                    qty = item.get("quantity")
                    qty_str = f"{qty} {item.get('unit', '')}" if qty is not None else "qty not stated"
                    st.markdown(f"- [{item.get('category', '-')}] {item.get('description', '')} ({qty_str})")
            if data.get("flags"):
                st.warning("Flags to review:\n" + "\n".join(f"- {f}" for f in data["flags"]))

    with col2:
        st.subheader("Visual Automated Takeoff")
        page_to_view = st.number_input("Blueprint Page Index", min_value=0, value=0, step=1)

        try:
            cv_img = VisionTakeoff.pdf_page_to_image(pdf_path, page_num=page_to_view)
        except VisionError as exc:
            st.error(str(exc))
            cv_img = None

        if cv_img is not None:
            st.info(
                "Upload a cropped symbol image (door, window, outlet) to "
                "auto-count matching symbols on this page. Works best when "
                "the template is cropped from this same document."
            )
            uploaded_template = st.file_uploader("Upload Target Symbol Template (PNG/JPG)", type=["png", "jpg"])

            if uploaded_template:
                try:
                    file_bytes = np.asarray(bytearray(uploaded_template.read()), dtype=np.uint8)
                    template_img = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
                    annotated_img, count_found = VisionTakeoff.count_symbols(cv_img.copy(), template_img)
                    st.session_state.counted_items[uploaded_template.name] = count_found

                    st.metric("Auto-Count Detected Instances", count_found)
                    st.image(cv2.cvtColor(annotated_img, cv2.COLOR_BGR2RGB), caption="Processed Takeoff Preview")
                except VisionError as exc:
                    st.error(str(exc))
            else:
                sharp_page_viewer(pdf_path, page_to_view)

    st.sidebar.markdown("---")
    has_results = st.session_state.parsed_data or st.session_state.counted_items
    if not has_results:
        st.sidebar.caption("Run scope extraction or symbol counting above before exporting.")

    if st.sidebar.button("Export Master Excel Estimation Workbook", disabled=not has_results):
        excel_path = f"temp/{uploaded_pdf.name.replace('.pdf', '')}_estimate.xlsx"
        try:
            ExcelGenerator.create_estimation_sheet(
                output_path=excel_path,
                project_name=uploaded_pdf.name,
                region=region,
                parsed_data=st.session_state.parsed_data,
                counted_items=st.session_state.counted_items,
            )
            with open(excel_path, "rb") as file:
                st.sidebar.download_button(
                    label="Download Excel Project Sheet",
                    data=file,
                    file_name=os.path.basename(excel_path),
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            st.sidebar.success("Workbook built - deliver to your estimator for review.")
        except Exception as exc:
            st.sidebar.error(f"Could not build the workbook: {exc}")
else:
    st.warning("Upload a PDF blueprint using the sidebar to begin.")
    st.session_state.parsed_data = None
    st.session_state.counted_items = {}
