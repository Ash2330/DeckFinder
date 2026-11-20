import base64
import binascii
from io import BytesIO
from zipfile import ZipFile

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.enum.text import MSO_AUTO_SIZE
from pptx.util import Inches, Pt
from pydantic import BaseModel, Field


class Slide(BaseModel):
    title: str = Field(default="", max_length=160)
    body: str = Field(default="", max_length=1800)
    layout: str = Field(default="content", pattern="^(content|section)$")
    image: str = Field(default="", max_length=280000)


class Draft(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    course: str = Field(default="", max_length=100)
    slides: list[Slide] = Field(min_length=1, max_length=100)


def render_pptx(draft: Draft) -> bytes:
    deck = Presentation()
    deck.core_properties.title = draft.title
    for entry in draft.slides:
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        width = 4.8 if entry.image else 9
        top = 1.65 if entry.layout == "section" else 0.6
        title = slide.shapes.add_textbox(Inches(0.5), Inches(top), Inches(9), Inches(1))
        title.text_frame.word_wrap = True
        title.text_frame.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        title.text_frame.text = entry.title
        title.text_frame.paragraphs[0].font.size = Pt(36 if entry.layout == "section" else 30)
        body = slide.shapes.add_textbox(Inches(0.5), Inches(top + 1.2), Inches(width), Inches(4.4))
        body.text_frame.word_wrap = True
        body.text_frame.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        body.text_frame.text = entry.body
        for paragraph in body.text_frame.paragraphs:
            paragraph.font.size = Pt(18)
        if entry.image:
            prefix, _, data = entry.image.partition(",")
            if prefix not in {"data:image/png;base64", "data:image/jpeg;base64"}:
                raise ValueError("Slide images must be PNG or JPEG.")
            try:
                picture = slide.shapes.add_picture(BytesIO(base64.b64decode(data, validate=True)),
                                                   Inches(5.6), Inches(top + 1.2), width=Inches(3.9))
            except (ValueError, OSError, binascii.Error) as exc:
                raise ValueError("A slide image could not be opened.") from exc
            if picture.height > Inches(3.25):
                picture.width = int(picture.width * Inches(3.25) / picture.height)
                picture.height = Inches(3.25)
    output = BytesIO()
    deck.save(output)
    return output.getvalue()


def shape_text(shapes):
    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from shape_text(shape.shapes)
        elif shape.has_table:
            yield from (cell.text for row in shape.table.rows for cell in row.cells)
        elif shape.has_text_frame:
            yield shape.text


def open_deck(data: bytes):
    with ZipFile(BytesIO(data)) as package:
        if sum(item.file_size for item in package.infolist()) > 120 * 1024 * 1024:
            raise ValueError("The uncompressed presentation is too large.")
        if "ppt/presentation.xml" not in package.namelist():
            raise ValueError("The upload must be a PowerPoint presentation.")
    return Presentation(BytesIO(data))


def slide_chunks(data: bytes):
    for number, slide in enumerate(open_deck(data).slides, 1):
        placeholder = slide.shapes.title
        title = placeholder.text.strip() if placeholder is not None else ""
        texts = [text.strip() for text in shape_text(slide.shapes) if text.strip()]
        if not title and texts:
            title = texts[0].splitlines()[0][:160]
        if title and title in texts:
            texts.remove(title)
        words = " ".join(texts).split()
        if not words and title:
            yield number, 0, title, title
        for index, start in enumerate(range(0, len(words), 120)):
            end = min(start + 150, len(words))
            yield number, index, title, " ".join(words[start:end])
            if end == len(words):
                break
