from PyPDF2 import PdfReader, PageObject
from PyPDF2.generic import IndirectObject, PdfObject
from PyPDF2.constants import AnnotationDictionaryAttributes, FieldDictionaryAttributes
from dataclasses import dataclass
import typing
import pypdfium as PDFIUM
import sys
import logging
import asyncio
import ctypes
from PIL import Image, ImageDraw

LOGGER = logging.getLogger(__name__)
PDFIUM.FPDF_InitLibraryWithConfig(PDFIUM.FPDF_LIBRARY_CONFIG(2, None, None, 0))


async def convert_rect(height: float, rect: list[float]) -> tuple[float]:
    """
    :rect: [bottom_left_x, bottom_left_y, top_right_x, top_right_y]
    """
    assert len(rect) == 4

    return (rect[0], height - rect[1], rect[2], height - rect[3])


@dataclass
class AnnotationAttribute:
    rect: list[float]
    border: list[int]
    value: typing.Any
    default_value: typing.Any


@dataclass
class PageAttribute:
    page_index: int  # 0 first
    width: int
    height: int
    annotation_attributes: list[AnnotationAttribute]


async def parse_page(page: PageObject, page_idx: int) -> PageAttribute:
    page_attributes: list[AnnotationAttribute] = []
    annotations: list[IndirectObject] = page.annotations

    for annotation in annotations:
        annotation_object = annotation.get_object()
        if not annotation_object:
            continue

        # we care about form field value, default value, border color, rectangle
        annotation_object_dict = dict(annotation_object)
        rectangle: list[float] = annotation_object_dict.get(
            AnnotationDictionaryAttributes.Rect, []
        )
        border: list[int] = annotation_object_dict.get(
            AnnotationDictionaryAttributes.Border, []
        )
        value = annotation_object_dict.get(FieldDictionaryAttributes.V, None)
        default_value = annotation_object_dict.get(FieldDictionaryAttributes.DV, None)

        page_attributes.append(
            AnnotationAttribute(rectangle, border, value, default_value)
        )

    return PageAttribute(
        page_idx, page.mediabox.width, page.mediabox.height, page_attributes
    )


async def construct_image_from_page(page: typing.Any, page_attributes: PageAttribute):
    # render to bitmap
    try:
        bitmap = PDFIUM.FPDFBitmap_Create(
            page_attributes.width, page_attributes.height, 0
        )
        PDFIUM.FPDFBitmap_FillRect(
            bitmap, 0, 0, page_attributes.width, page_attributes.height, 0xFFFFFFFF
        )
        PDFIUM.FPDF_RenderPageBitmap(
            bitmap,
            page,
            0,
            0,
            page_attributes.width,
            page_attributes.height,
            PDFIUM.FPDF_LCD_TEXT | PDFIUM.FPDF_ANNOT,
        )
        buffer = PDFIUM.FPDFBitmap_GetBuffer(bitmap)
        cast_buffer = ctypes.cast(
            buffer,
            ctypes.POINTER(
                ctypes.c_ubyte * (page_attributes.width * page_attributes.height * 4)
            ),
        )
        image = Image.frombuffer(
            "RGBA",
            (page_attributes.width, page_attributes.height),
            cast_buffer.contents,
            "raw",
            "BGRA",
            0,
            1,
        )

        artist = ImageDraw.Draw(image)

        for attribute in page_attributes.annotation_attributes:
            converted_rect = await convert_rect(attribute.rect)

        # logi here

        # PDFIUM.FPDF_ClosePage(page)

    except Exception as e:
        LOGGER.error(f"Error constructing image: {e}. Exiting...")
        sys.exit(1)


async def construct_image_from_file(
    file_name: str, file_attributes: list[PageAttribute]
):
    try:
        pdf_file = PDFIUM.FPDF_LoadDocument(file_name, None)
    except Exception as e:
        LOGGER.error(f"failed loading file {e}. exiting...")
        sys.exit(1)
    else:
        for page_attributes in file_attributes:
            page = PDFIUM.FPDF_LoadPage(pdf_file, page_attributes.page_index)


async def parse_file(file_name: str) -> list[PageAttribute]:
    assert file_name.lower().endswith(".pdf")

    LOGGER.info(f"parsing file {file_name}...")
    try:
        pdf_file = PdfReader(file_name, True)
    except Exception as e:
        LOGGER.error(f"error: {e}")
        sys.exit(1)
    else:
        if len(pdf_file.pages) == 0:
            LOGGER.warning("file has no page. Exiting...")
            sys.exit(1)

        result: list[PageAttribute] = []
        for page_idx, page in enumerate(pdf_file.pages):
            page_attributes = await parse_page(page, page_idx)
            result.append(page_attributes)

        return result


async def main(file_names: list[str]):
    for file in file_names:
        page_attributes = await parse_file(file)


if __name__ == "__main__":
    assert len(sys.argv) >= 2

    # main(["file.pdf"])
    asyncio.run(main(sys.argv[1:]))

