from PyPDF2 import PdfReader, PageObject
from PyPDF2.generic import IndirectObject
from PyPDF2.constants import (
    AnnotationDictionaryAttributes as AnnDictAttrs,
    FieldDictionaryAttributes as FieldDictAttrs,
)
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
FORM_FIELD_COLOR = "#cbd4d6"


def convert_rect(page_height: float, rect: list[float]) -> tuple[float]:
    """
    :rect: [bottom_left_x, bottom_left_y, top_right_x, top_right_y]
    """
    assert len(rect) == 4

    return (
        rect[0],
        float(page_height - rect[3]),
        rect[2],
        float(page_height - rect[1]),
    )


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
        rectangle: list[float] = annotation_object_dict.get(AnnDictAttrs.Rect, [])
        border: list[int] = annotation_object_dict.get(AnnDictAttrs.Border, [])
        value = annotation_object_dict.get(FieldDictAttrs.V, "")
        default_value = annotation_object_dict.get(FieldDictAttrs.DV, "")

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
            page_attributes.width, page_attributes.height, 1
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
            0,
            # PDFIUM.FPDF_ANNOT | PDFIUM.FPDF_LCD_TEXT,
            0,
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
            converted_rect = convert_rect(page_attributes.height, attribute.rect)
            artist.rectangle(converted_rect, FORM_FIELD_COLOR)
            # print(page_attributes.height)
            # print(converted_rect)

        # logi here

        # free resource
        PDFIUM.FPDF_ClosePage(page)
        if bitmap:
            PDFIUM.FPDFBitmap_Destroy(bitmap)

        return image

    except Exception as e:
        LOGGER.error(
            f"Error constructing image: {e}, page {page_attributes.page_index}. Exiting..."
        )
        sys.exit(1)


async def construct_image_from_file(
    file_name: str, file_attributes: list[PageAttribute]
):
    out_file_name = file_name.lower().rsplit(".", 1)[0]
    try:
        pdf_file = PDFIUM.FPDF_LoadDocument(file_name, None)

        total_height = 0
        max_page_width = 0

        for page_attributes in file_attributes:
            total_height += page_attributes.height
            if page_attributes.width > max_page_width:
                max_page_width = page_attributes.width

        images = await asyncio.gather(
            *[
                construct_image_from_page(
                    PDFIUM.FPDF_LoadPage(pdf_file, page_attributes.page_index),
                    page_attributes,
                )
                for page_attributes in file_attributes
            ]
        )

        height_track = 0
        output_image = Image.new("RGB", (max_page_width, total_height), 0xFFFFFF)
        for image in images:
            output_image.paste(image, (0, height_track))
            height_track += image.height

            # free resource
            image.close()

        output_image.save(f"{out_file_name}.jpg", "JPEG", subsampling=0, quality=100)

        # free resource
        PDFIUM.FPDF_CloseDocument(pdf_file)
        output_image.close()

    except Exception as e:
        LOGGER.error(f"failed loading file {e}. exiting...")
        sys.exit(1)


async def parse_file(file_name: str) -> list[PageAttribute]:
    assert file_name.lower().endswith(".pdf")
    LOGGER.info(f"parsing file {file_name}...")

    try:
        pdf_file = PdfReader(file_name, True)
        if len(pdf_file.pages) == 0:
            LOGGER.warning(f"file {file_name} has no page. Exiting...")
            sys.exit(1)

        result: list[PageAttribute] = []
        for page_idx, page in enumerate(pdf_file.pages):
            page_attributes = await parse_page(page, page_idx)
            result.append(page_attributes)

        return result

    except Exception as e:
        LOGGER.error(f"error: {e}")
        sys.exit(1)


async def main(file_names: list[str]):
    await asyncio.gather(
        *[
            construct_image_from_file(file_name, await parse_file(file_name))
            for file_name in file_names
        ]
    )


if __name__ == "__main__":
    assert len(sys.argv) >= 2
    asyncio.run(main(sys.argv[1:]))
