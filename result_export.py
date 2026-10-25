"""Downloadable exports for Cognisense analyzer results."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from textwrap import wrap
from typing import Any
from unicodedata import normalize
from xml.sax.saxutils import escape


EXPORT_FORMATS = {
    "json": ("application/json", "json"),
    "csv": ("text/csv; charset=utf-8", "csv"),
    "pdf": ("application/pdf", "pdf"),
}


def _walk(value: Any, path: str = ""):
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            yield from _walk(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, f"{path}[{index}]")


def _summary_and_reasoning(result: Any) -> tuple[str, str]:
    summary: list[str] = []
    reasoning: list[str] = []
    for path, value in _walk(result):
        key = path.rsplit(".", 1)[-1].split("[", 1)[0].lower()
        if not isinstance(value, str):
            continue
        if key in {"summary", "message"} and value and value not in summary:
            summary.append(value)
        elif key in {"why", "reasoning", "ai_reasoning"} and value and value not in reasoning:
            reasoning.append(value)
    return " | ".join(summary), " | ".join(reasoning)


def _chart_data(results: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    charts: dict[str, list[dict[str, Any]]] = {
        "sentiment_scores": [],
        "word_scores": [],
        "sentence_scores": [],
        "frame_brightness": [],
        "emotion_scores": [],
    }

    for index, item in enumerate(results):
        result = item.get("result")
        modality = str(item.get("modality", "unknown"))
        source = str(item.get("source", f"input-{index + 1}"))
        if not isinstance(result, dict):
            continue

        for path, value in _walk(result):
            if not isinstance(value, dict):
                continue
            label = value.get("label")
            score = value.get("score")
            lower_path = path.lower()
            if isinstance(label, str) and isinstance(score, (int, float)) and not isinstance(score, bool):
                point = {
                    "modality": modality,
                    "source": source,
                    "result_index": index,
                    "label": label,
                    "score": score,
                    "path": path,
                }
                chart = "sentiment_scores"
                if "word_level" in lower_path:
                    chart = "word_scores"
                elif "sentence_level" in lower_path:
                    chart = "sentence_scores"
                elif "emotions" in lower_path:
                    chart = "emotion_scores"
                if point not in charts[chart]:
                    charts[chart].append(point)

            if (path == "emotions" or path.endswith(".emotions")) and isinstance(value, dict):
                for emotion, emotion_score in value.items():
                    if isinstance(emotion_score, (int, float)) and not isinstance(emotion_score, bool):
                        charts["emotion_scores"].append(
                            {
                                "modality": modality,
                                "source": source,
                                "result_index": index,
                                "label": str(emotion),
                                "score": emotion_score,
                                "path": f"{path}.{emotion}",
                            }
                        )

        if isinstance(result.get("frame_brightness"), dict):
            for frame in result["frame_brightness"].get("frames", []):
                if isinstance(frame, dict) and isinstance(frame.get("brightness"), (int, float)):
                    charts["frame_brightness"].append(
                        {
                            "modality": modality,
                            "source": source,
                            "result_index": index,
                            "time": frame.get("time"),
                            "brightness": frame["brightness"],
                        }
                    )

    return charts


def _overall_sentiment(result: dict[str, Any]) -> dict[str, Any]:
    candidates: dict[str, list[dict[str, Any]]] = {
        "combined_sentiment": [],
        "combined": [],
        "overall": [],
        "top_emotion": [],
    }
    for path, value in _walk(result):
        key = path.rsplit(".", 1)[-1].lower()
        if key in candidates and isinstance(value, dict) and ("label" in value or "score" in value):
            candidates[key].append(value)
    return next((values[0] for values in candidates.values() if values), {})


def _export_payload(
    project_id: str,
    request_id: str,
    results: list[dict[str, Any]],
    analysis_timestamp: str,
) -> dict[str, Any]:
    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "analysis_timestamp": analysis_timestamp,
        "project_id": project_id,
        "request_id": request_id,
        "data": {
            "status": "partial" if any("error" in item for item in results) else "complete",
            "results": results,
            "chart_data": _chart_data(results),
        },
    }


def _to_csv(payload: dict[str, Any]) -> bytes:
    output = io.StringIO(newline="")
    fields = [
        "analysis_timestamp",
        "exported_at",
        "project_id",
        "request_id",
        "modality",
        "source",
        "sentiment_label",
        "sentiment_score",
        "summary",
        "reasoning",
        "chart_type",
        "chart_label",
        "chart_value",
        "chart_index",
        "result_json",
    ]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()

    def safe_cell(value: Any) -> Any:
        if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
            return "'" + value
        return value

    charts = payload["data"]["chart_data"]
    chart_rows = [
        (chart_name, point)
        for chart_name, points in charts.items()
        for point in points
    ]

    for item_index, item in enumerate(payload["data"]["results"]):
        result = item.get("result")
        if not isinstance(result, dict):
            result = {}
        sentiment = _overall_sentiment(result)
        summary, reasoning = _summary_and_reasoning(result)
        item_charts = [
            (chart_name, point)
            for chart_name, point in chart_rows
            if point.get("result_index") == item_index
        ]
        base_row = {
            "analysis_timestamp": payload["analysis_timestamp"],
            "exported_at": payload["exported_at"],
            "project_id": payload["project_id"],
            "request_id": payload["request_id"],
            "modality": item.get("modality"),
            "source": item.get("source"),
            "sentiment_label": sentiment.get("label"),
            "sentiment_score": sentiment.get("score"),
            "summary": summary,
            "reasoning": reasoning,
            "result_json": json.dumps(item, ensure_ascii=False, separators=(",", ":")),
        }
        if item_charts:
            for chart_name, point in item_charts:
                value = point.get("score", point.get("brightness"))
                writer.writerow(
                    {
                        **{key: safe_cell(value) for key, value in base_row.items()},
                        "chart_type": chart_name,
                        "chart_label": safe_cell(point.get("label", point.get("source"))),
                        "chart_value": value,
                        "chart_index": point.get("time"),
                    }
                )
        else:
            writer.writerow({key: safe_cell(value) for key, value in base_row.items()})
    return output.getvalue().encode("utf-8-sig")


def _paragraph(text: Any, style: Any) -> Any:
    from reportlab.platypus import Paragraph

    plain_text = normalize("NFKD", str(text)).encode("ascii", "replace").decode("ascii")
    plain_text = "".join(char for char in plain_text if ord(char) >= 32 or char in "\n\r\t")
    return Paragraph(escape(plain_text), style)


def _pdf_chart(
    title: str,
    points: list[dict[str, Any]],
    value_key: str,
    label_key: str,
    *,
    axis_label: str,
    score_range: tuple[float, float] | None = None,
    sentiment_legend: bool = False,
) -> Any:
    from reportlab.graphics.shapes import Drawing, Line, Rect, String
    from reportlab.lib import colors

    valid_points = [
        point
        for point in points
        if isinstance(point.get(value_key), (int, float))
        and not isinstance(point.get(value_key), bool)
    ]
    if not valid_points:
        return None

    chart_points = valid_points[:12]
    values = [float(point[value_key]) for point in chart_points]
    if score_range:
        value_min, value_max = score_range
    elif min(values) < 0:
        value_min, value_max = -1.0, 1.0
    else:
        value_min, value_max = 0.0, max(1.0, max(values))

    width = 468
    wrapped_labels = []
    row_heights = []
    for index, point in enumerate(chart_points):
        label = str(point.get(label_key, point.get("source", f"Item {index + 1}")))
        safe_label = normalize("NFKD", label).encode("ascii", "replace").decode("ascii")
        lines = wrap(safe_label, width=32, break_long_words=True, break_on_hyphens=False) or [""]
        wrapped_labels.append(lines)
        row_heights.append(max(22, len(lines) * 9 + 4))
    top = 60 if sentiment_legend else 44
    height = top + sum(row_heights) + 24
    drawing = Drawing(width, height)
    drawing.add(String(4, height - 14, title, fontName="Helvetica-Bold", fontSize=10))
    if sentiment_legend:
        legend_y = height - 31
        for legend_index, (legend_label, legend_color) in enumerate((
            ("Negative", "#C94C4C"),
            ("Neutral", "#8A929B"),
            ("Positive", "#3A8D68"),
        )):
            legend_x = 155 + legend_index * 88
            drawing.add(Rect(legend_x, legend_y, 7, 7, fillColor=colors.HexColor(legend_color), strokeColor=None))
            drawing.add(String(legend_x + 11, legend_y, legend_label, fontName="Helvetica", fontSize=7))
    label_width = 145
    plot_left = label_width + 8
    plot_width = 250
    plot_right = plot_left + plot_width
    zero_x = plot_left + (0 - value_min) / (value_max - value_min) * plot_width
    drawing.add(Line(plot_left, 22, plot_right, 22, strokeColor=colors.HexColor("#89939D"), strokeWidth=0.6))
    drawing.add(Line(zero_x, 24, zero_x, height - 28, strokeColor=colors.HexColor("#89939D"), strokeWidth=0.7))

    for tick_index in range(5):
        tick_value = value_min + (value_max - value_min) * tick_index / 4
        tick_x = plot_left + plot_width * tick_index / 4
        drawing.add(Line(tick_x, 20, tick_x, 24, strokeColor=colors.HexColor("#89939D"), strokeWidth=0.6))
        drawing.add(String(tick_x - 8, 8, f"{tick_value:.1f}", fontName="Helvetica", fontSize=7))
    drawing.add(String(plot_left + 58, 0, axis_label, fontName="Helvetica", fontSize=7))

    row_top = height - top
    for index, point in enumerate(chart_points):
        row_height = row_heights[index]
        y = row_top - row_height + (row_height - 10) / 2
        for line_index, line in enumerate(wrapped_labels[index]):
            drawing.add(String(4, row_top - 8 - line_index * 9, line, fontName="Helvetica", fontSize=7.2))
        value = float(point[value_key])
        end_x = plot_left + (value - value_min) / (value_max - value_min) * plot_width
        bar_x = min(zero_x, end_x)
        bar_width = max(1.0, abs(end_x - zero_x))
        sentiment = str(point.get("sentiment", point.get("label", ""))).lower()
        if any(term in sentiment for term in ("negative", "sad", "angry", "anger", "fear", "disgust")):
            fill = colors.HexColor("#C94C4C")
        elif any(term in sentiment for term in ("positive", "happy", "joy", "surprise")):
            fill = colors.HexColor("#3A8D68")
        elif any(term in sentiment for term in ("neutral",)):
            fill = colors.HexColor("#8A929B")
        else:
            fill = colors.HexColor("#5E7F78")
        drawing.add(Rect(bar_x, y, bar_width, 10, fillColor=fill, strokeColor=None))
        value_text = f"{sentiment.title()} {value:.3f}".strip()
        drawing.add(String(plot_right + 8, y + 2, value_text, fontName="Helvetica", fontSize=7))
        row_top -= row_height
    return drawing


def _pdf_cell(value: Any, style: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        if isinstance(value, dict):
            text = "; ".join(f"{key}: {_readable_value(child)}" for key, child in value.items())
        else:
            text = "; ".join(_readable_value(child) for child in value)
    else:
        text = _readable_value(value)
    return _paragraph(text, style)


def _readable_value(value: Any) -> str:
    if value is None:
        return "Not provided"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    if isinstance(value, dict):
        return "; ".join(f"{key}: {_readable_value(child)}" for key, child in value.items())
    if isinstance(value, (list, tuple)):
        return "; ".join(_readable_value(child) for child in value)
    return str(value)


def _pdf_table(rows: list[list[Any]], colors: Any, *, header: bool = False, widths: list[float] | None = None) -> Any:
    from reportlab.platypus import Table, TableStyle

    table = Table(rows, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT")
    style = [
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#D7DDE1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    if header:
        style.extend([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EFEC")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#243A34")),
        ])
    elif len(rows) and len(rows[0]) == 2:
        style.append(("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F1F4F3")))
    table.setStyle(TableStyle(style))
    return table


def _append_result_data(story: list[Any], data: Any, heading_style: Any, body_style: Any, small_style: Any, colors: Any, level: int = 0) -> None:
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    if isinstance(data, dict):
        scalar_rows = []
        nested_values = []
        for key, value in data.items():
            if isinstance(value, (dict, list, tuple)):
                if value:
                    nested_values.append((key, value))
            elif value is not None and value != "":
                scalar_rows.append([_paragraph(str(key).replace("_", " ").title(), small_style), _pdf_cell(value, body_style)])
        if scalar_rows:
            story.append(_pdf_table(scalar_rows, colors, widths=[1.55 * inch, 5.05 * inch]))
            story.append(Spacer(1, 5))
        for key, value in nested_values:
            story.append(Paragraph(str(key).replace("_", " ").title(), heading_style))
            _append_result_data(story, value, heading_style, body_style, small_style, colors, level + 1)
        return

    if isinstance(data, (list, tuple)):
        if not data:
            return
        if all(isinstance(item, dict) for item in data):
            columns = list(dict.fromkeys(key for item in data for key in item))
            if not columns:
                return
            rows = [[_paragraph(str(column).replace("_", " ").title(), small_style) for column in columns]]
            for item in data:
                rows.append([_pdf_cell(item.get(column), body_style) for column in columns])
            story.append(_pdf_table(rows, colors, header=True))
        else:
            rows = [[_pdf_cell(item, body_style)] for item in data]
            story.append(_pdf_table(rows, colors))
        story.append(Spacer(1, 5))
        return

    if data is None or data == "":
        return
    story.append(_pdf_table(
        [[_paragraph("Value", small_style), _pdf_cell(data, body_style)]],
        colors,
        widths=[1.55 * inch, 5.05 * inch],
    ))


def _sentiment_chart_points(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    points = []
    for index, item in enumerate(items, start=1):
        result = item.get("result")
        if not isinstance(result, dict):
            continue
        sentiment = _overall_sentiment(result)
        score = sentiment.get("score")
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            continue
        points.append({
            "label": str(item.get("source") or f"Input {index}"),
            "score": score,
            "sentiment": sentiment.get("label", ""),
        })
    return points


def _to_pdf(payload: dict[str, Any]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    output = io.BytesIO()
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "CognisenseTitle",
        parent=styles["Title"],
        alignment=TA_LEFT,
        fontName="Helvetica-Bold",
        fontSize=22,
        leading=26,
        textColor=colors.HexColor("#243A34"),
        spaceAfter=4,
    )
    heading_style = ParagraphStyle(
        "ReportHeading",
        parent=styles["Heading3"],
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=13,
        textColor=colors.HexColor("#315B4B"),
        spaceBefore=7,
        spaceAfter=4,
    )
    body_style = ParagraphStyle("ReportBody", parent=styles["BodyText"], fontSize=8.5, leading=12, spaceAfter=3)
    small_style = ParagraphStyle("ReportSmall", parent=body_style, fontSize=7.5, leading=10)
    transcript_style = ParagraphStyle("Transcript", parent=body_style, leading=13, alignment=TA_LEFT)

    def draw_page(canvas: Any, document: Any) -> None:
        canvas.saveState()
        page_width, page_height = letter
        canvas.setStrokeColor(colors.HexColor("#3A8D68"))
        canvas.setLineWidth(2)
        canvas.line(document.leftMargin, page_height - 0.38 * inch, page_width - document.rightMargin, page_height - 0.38 * inch)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#68746F"))
        canvas.drawString(document.leftMargin, 0.34 * inch, "Cognisense Analysis Report")
        canvas.drawRightString(page_width - document.rightMargin, 0.34 * inch, f"Page {document.page}")
        canvas.restoreState()

    document = SimpleDocTemplate(
        output,
        pagesize=letter,
        rightMargin=0.65 * inch,
        leftMargin=0.65 * inch,
        topMargin=0.62 * inch,
        bottomMargin=0.58 * inch,
        title="Cognisense Analysis Report",
        author="Cognisense",
    )
    story: list[Any] = [
        Paragraph("Cognisense", title_style),
        Paragraph("Analysis report", styles["Heading2"]),
        Spacer(1, 5),
    ]
    metadata = [
        [_paragraph("Project ID", small_style), _paragraph(payload["project_id"], body_style)],
        [_paragraph("Analysis date", small_style), _paragraph(payload["analysis_timestamp"], body_style)],
        [_paragraph("Request ID", small_style), _paragraph(payload["request_id"], body_style)],
    ]
    story.append(_pdf_table(metadata, colors, widths=[1.55 * inch, 5.05 * inch]))
    story.extend([Spacer(1, 10), Paragraph("Overall sentiment", heading_style)])

    items = payload["data"]["results"]
    sentiment_rows = [[_paragraph("File", small_style), _paragraph("Modality", small_style), _paragraph("Sentiment", small_style), _paragraph("Score", small_style)]]
    for index, item in enumerate(items, start=1):
        result = item.get("result")
        sentiment = _overall_sentiment(result) if isinstance(result, dict) else {}
        if not sentiment:
            continue
        sentiment_rows.append([
            _pdf_cell(item.get("source") or f"Input {index}", body_style),
            _pdf_cell(item.get("modality", "unknown"), body_style),
            _pdf_cell(sentiment.get("label", "Not provided"), body_style),
            _pdf_cell(sentiment.get("score", "Not provided"), body_style),
        ])
    if len(sentiment_rows) > 1:
        story.append(_pdf_table(sentiment_rows, colors, header=True, widths=[2.35 * inch, 1.0 * inch, 1.65 * inch, 1.6 * inch]))
        overall_chart = _pdf_chart(
            "Overall scores by file",
            _sentiment_chart_points(items),
            "score",
            "label",
            axis_label="Sentiment score (-1 to +1); image confidence (0 to 1)",
            score_range=(-1.0, 1.0),
            sentiment_legend=True,
        )
        if overall_chart:
            story.extend([Spacer(1, 7), overall_chart, Spacer(1, 8)])
    else:
        story.append(_paragraph("No overall sentiment score was provided by the analyzers.", body_style))

    findings = []
    for item in items:
        result = item.get("result")
        if not isinstance(result, dict):
            continue
        summary, _ = _summary_and_reasoning(result)
        if summary:
            findings.append([_pdf_cell(item.get("source", "Input"), body_style), _pdf_cell(summary, body_style)])
    if findings:
        story.extend([Paragraph("Key findings", heading_style), _pdf_table(
            [[_paragraph("File", small_style), _paragraph("Finding", small_style)]] + findings,
            colors,
            header=True,
            widths=[1.55 * inch, 5.05 * inch],
        ), Spacer(1, 8)])

    for index, item in enumerate(items, start=1):
        result = item.get("result")
        if not isinstance(result, dict):
            result = {}
        modality = str(item.get("modality", "unknown")).replace("_", " ").title()
        source = str(item.get("source") or f"Input {index}")
        story.append(_paragraph(f"{modality}: {source}", styles["Heading2"]))
        if item.get("error") is not None:
            story.extend([Paragraph("Analysis status", heading_style), _pdf_table(
                [[_pdf_cell("Error", small_style), _pdf_cell(item["error"], body_style)]],
                colors,
                widths=[1.55 * inch, 5.05 * inch],
            )])

        sentiment = _overall_sentiment(result)
        if sentiment:
            sentiment_label = str(sentiment.get("label", "")).lower()
            score = sentiment.get("score")
            score_points = [{"label": sentiment.get("label", "Sentiment"), "score": score, "sentiment": sentiment_label}]
            chart = _pdf_chart(
                "Overall sentiment score",
                score_points,
                "score",
                "label",
                axis_label="Sentiment score (-1 to +1)" if modality.lower() != "image" else "Emotion confidence (0 to 1)",
                score_range=(0.0, 1.0) if modality.lower() == "image" else (-1.0, 1.0),
                sentiment_legend=modality.lower() != "image",
            )
            if chart:
                story.extend([chart, Spacer(1, 5)])

        for path, value in _walk(result):
            if not isinstance(value, list) or not value or not all(isinstance(row, dict) for row in value):
                continue
            path_lower = path.lower()
            chart_points = []
            if "word_level" in path_lower or "sentence_level" in path_lower:
                for point_index, row in enumerate(value, start=1):
                    point_score = row.get("score")
                    if not isinstance(point_score, (int, float)) or isinstance(point_score, bool):
                        continue
                    label = row.get("word") if "word_level" in path_lower else f"Sentence {point_index}"
                    chart_points.append({"label": label, "score": point_score, "sentiment": row.get("label", "")})
                if chart_points:
                    chart = _pdf_chart(
                        "Word-level sentiment scores" if "word_level" in path_lower else "Sentence-level sentiment scores",
                        chart_points,
                        "score",
                        "label",
                        axis_label="Sentiment score (-1 negative to +1 positive)",
                        score_range=(-1.0, 1.0),
                        sentiment_legend=True,
                    )
                    if chart:
                        story.extend([chart, Spacer(1, 5)])

        for path, value in _walk(result):
            if not isinstance(value, dict):
                continue
            path_lower = path.lower()
            if path_lower.endswith("emotions") and value and all(isinstance(score, (int, float)) and not isinstance(score, bool) for score in value.values()):
                emotion_points = [{"label": label, "score": score, "sentiment": label} for label, score in value.items()]
                chart = _pdf_chart("Emotion confidence scores", emotion_points, "score", "label", axis_label="Confidence (0 to 1)", score_range=(0.0, 1.0))
                if chart:
                    story.extend([chart, Spacer(1, 5)])
            if path_lower.endswith("frame_brightness"):
                frames = value.get("frames", [])
                frame_points = [
                    {"label": f"Time {frame.get('time', frame_index)}", "brightness": frame.get("brightness")}
                    for frame_index, frame in enumerate(frames, start=1)
                    if isinstance(frame, dict)
                ]
                chart = _pdf_chart("Video frame brightness by timestamp", frame_points, "brightness", "label", axis_label="Brightness (0 to 255)", score_range=(0.0, 255.0))
                if chart:
                    story.extend([chart, Spacer(1, 5)])

        has_result_content = any(value is not None and value != "" and value != [] and value != {} for value in result.values())
        if has_result_content:
            story.append(Paragraph("Analyzer details", heading_style))
        for key, value in result.items():
            if value is None or value == "" or value == [] or value == {}:
                continue
            field_title = str(key).replace("_", " ").title()
            if key.lower() in {"transcription", "transcript"} and isinstance(value, str) and value:
                story.extend([Paragraph("Full transcript", heading_style), _paragraph(value, transcript_style), Spacer(1, 6)])
                continue
            story.append(Paragraph(field_title, heading_style))
            _append_result_data(story, value, heading_style, body_style, small_style, colors)
        story.append(Spacer(1, 8))

    document.build(story, onFirstPage=draw_page, onLaterPages=draw_page)
    return output.getvalue()


def build_export(
    export_format: str,
    project_id: str,
    request_id: str,
    results: list[dict[str, Any]],
    analysis_timestamp: str,
) -> tuple[bytes, str, str]:
    """Serialize authenticated analyzer outputs and return bytes, media type, and extension."""
    if export_format not in EXPORT_FORMATS:
        raise ValueError(f"Unsupported export format: {export_format}")
    content_type, extension = EXPORT_FORMATS[export_format]
    payload = _export_payload(project_id, request_id, results, analysis_timestamp)
    if export_format == "json":
        body = json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    elif export_format == "csv":
        body = _to_csv(payload)
    else:
        body = _to_pdf(payload)
    return body, content_type, extension
