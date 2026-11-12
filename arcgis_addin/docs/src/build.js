// 사용설명서 3개 언어 docx 생성: node build.js <출력 폴더>
const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, ImageRun, Table, TableRow, TableCell, WidthType, ShadingType,
  AlignmentType, HeadingLevel, LevelFormat, BorderStyle, PageBreak, Footer, PageNumber,
  TabStopType, Bookmark, InternalHyperlink,
} = require("docx");

const OUT = process.argv[2] || __dirname;
const IMG = path.join(__dirname, "img");
const dims = JSON.parse(fs.readFileSync(path.join(__dirname, "dims.json"), "utf8"));
const CONTENT_W = 9638; // A4 11906 - 여백 2×1134
const NAVY = "1F4E79";
const HANGUL = /([ᄀ-ᇿ㄰-㆏가-힯]+)/;

function build(c) {
  const kr = c.krFont ? { ascii: c.krFont, hAnsi: c.krFont, eastAsia: c.krFont, cs: c.krFont } : null;
  // 중문판: 한글 구간은 맑은 고딕(YaHei에 한글 글꼴 없음)
  const runs = (text, opt = {}) => {
    if (!kr) return [new TextRun({ text, font: c.font, ...opt })];
    return text.split(HANGUL).filter((s) => s).map((s) =>
      new TextRun({ text: s, font: HANGUL.test(s) ? kr : c.font, ...opt }));
  };
  const children = [];
  let fig = 0;
  let listNo = 0;
  const cell = (text, w, head) => new TableCell({
    width: { size: w, type: WidthType.DXA },
    shading: head ? { type: ShadingType.CLEAR, fill: NAVY, color: "auto" } : undefined,
    margins: { top: 60, bottom: 60, left: 100, right: 100 },
    children: [new Paragraph({ spacing: { before: 0, after: 0, line: 276 },
      children: runs(text, { bold: !!head, color: head ? "FFFFFF" : "222222", size: head ? 19 : 18 }) })],
  });
  const border = { style: BorderStyle.SINGLE, size: 4, color: "BFC5CC" };

  // 표지
  children.push(new Paragraph({ spacing: { before: 3200, after: 200 }, children: runs(c.title, { bold: true, size: 52, color: NAVY }) }));
  children.push(new Paragraph({ spacing: { after: 600 }, border: { bottom: { style: BorderStyle.SINGLE, size: 12, color: NAVY, space: 8 } },
    children: runs(c.subtitle, { size: 32, color: "333333" }) }));
  children.push(new Paragraph({ children: runs(c.meta, { size: 22, color: "555555" }) }));
  children.push(new Paragraph({ children: [new PageBreak()] }));
  // 목차
  children.push(new Paragraph({ spacing: { after: 200 }, children: runs(c.tocTitle, { bold: true, size: 32, color: NAVY }) }));
  // 정적 목차: 장·절 제목의 책갈피로 가는 문서 안 링크(필드 갱신이 필요 없어 어떤 뷰어에서도 바로 보인다)
  let hn = 0;
  c.blocks.filter(([k]) => k === "h1" || k === "h2").forEach(([k, t]) => {
    hn += 1;
    children.push(new Paragraph({
      indent: { left: k === "h1" ? 0 : 400 }, spacing: { before: k === "h1" ? 140 : 20, after: 20 },
      children: [new InternalHyperlink({ anchor: `h${hn}`,
        children: runs(t, { bold: k === "h1", size: k === "h1" ? 22 : 20, color: k === "h1" ? NAVY : "333333" }) })],
    }));
  });
  children.push(new Paragraph({ children: [new PageBreak()] }));

  hn = 0;
  const anchored = (t) => { hn += 1; return [new Bookmark({ id: `h${hn}`, children: runs(t) })]; };
  for (const [kind, a, b, d] of c.blocks) {
    if (kind === "h1") children.push(new Paragraph({ heading: HeadingLevel.HEADING_1, pageBreakBefore: a !== c.blocks[0][1], children: anchored(a) }));
    else if (kind === "h2") children.push(new Paragraph({ heading: HeadingLevel.HEADING_2, children: anchored(a) }));
    // h3: 목차에 넣지 않는 소제목(굵게, 다음 블록과 붙여 둔다)
    else if (kind === "h3") children.push(new Paragraph({ keepNext: true, spacing: { before: 160, after: 60 }, children: runs(a, { bold: true, size: 21, color: NAVY }) }));
    else if (kind === "p") children.push(new Paragraph({ spacing: { after: 120 }, children: runs(a) }));
    else if (kind === "ul") a.forEach((t) => children.push(new Paragraph({ numbering: { reference: "bul", level: 0 }, spacing: { after: 60 }, children: runs(t) })));
    else if (kind === "ol") {
      listNo += 1;
      a.forEach((t) => children.push(new Paragraph({ numbering: { reference: "num", level: 0, instance: listNo }, spacing: { after: 60 }, children: runs(t) })));
    } else if (kind === "note") {
      children.push(new Paragraph({
        spacing: { before: 120, after: 160 }, indent: { left: 120, right: 120 },
        shading: { type: ShadingType.CLEAR, fill: "FFF7E6", color: "auto" },
        border: { left: { style: BorderStyle.SINGLE, size: 24, color: "C98A00", space: 8 } },
        children: runs(a, { size: 19, color: "5A3E00" }),
      }));
    } else if (kind === "code") {
      a.forEach((t, i) => children.push(new Paragraph({
        spacing: { before: i === 0 ? 80 : 0, after: i === a.length - 1 ? 160 : 0, line: 260 },
        shading: { type: ShadingType.CLEAR, fill: "F2F4F6", color: "auto" }, indent: { left: 120 },
        children: (kr ? runs(t, { size: 16 }) : [new TextRun({ text: t, size: 16, font: { ascii: "Consolas", hAnsi: "Consolas", eastAsia: c.font.eastAsia, cs: "Consolas" } })]),
      })));
    } else if (kind === "table") {
      const widths = d;
      const rows = [new TableRow({ tableHeader: true, children: a.map((h, i) => cell(h, widths[i], true)) })]
        .concat(b.map((r) => new TableRow({ cantSplit: true, children: r.map((v, i) => cell(v, widths[i], false)) })));
      children.push(new Table({ width: { size: CONTENT_W, type: WidthType.DXA }, columnWidths: widths, rows,
        borders: { top: border, bottom: border, left: border, right: border, insideHorizontal: border, insideVertical: border } }));
      children.push(new Paragraph({ spacing: { after: 120 }, children: [] }));
    } else if (kind === "fig") {
      fig += 1;
      const [w, h] = dims[a];
      const inch = d || Math.min(6.6, w / 150);
      const px = Math.round(inch * 96);
      children.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 120, after: 60 }, keepNext: true,
        children: [new ImageRun({ type: a === "ribbon" ? "png" : "jpg", data: fs.readFileSync(path.join(IMG, a + (a === "ribbon" ? ".png" : ".jpg"))),
          transformation: { width: px, height: Math.round(px * h / w) } })] }));
      children.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 200 },
        children: runs(`${c.figure} ${fig}. ${b}`, { size: 18, color: "444444", bold: true }) }));
    }
  }

  const heading = (size, before, after, color) => ({ run: { font: c.font, size, bold: true, color }, paragraph: { spacing: { before, after }, keepNext: true } });
  return new Document({
    creator: "skywatch_poc", title: `${c.title} - ${c.subtitle}`,
    styles: {
      default: { document: { run: { font: c.font, size: 21 }, paragraph: { spacing: { line: 300 } } } },
      paragraphStyles: [
        { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true, ...heading(32, 240, 200, NAVY),
          paragraph: { spacing: { before: 0, after: 200 }, keepNext: true, outlineLevel: 0,
            border: { bottom: { style: BorderStyle.SINGLE, size: 8, color: NAVY, space: 4 } } } },
        { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true, ...heading(25, 280, 120, "222222"),
          paragraph: { spacing: { before: 280, after: 120 }, keepNext: true, outlineLevel: 1 } },
      ],
    },
    numbering: { config: [
      { reference: "bul", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
        style: { paragraph: { indent: { left: 400, hanging: 260 } } } }] },
      { reference: "num", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
        style: { paragraph: { indent: { left: 420, hanging: 300 } } } }] },
    ] },
    sections: [{
      properties: { page: { size: { width: 11906, height: 16838 }, margin: { top: 1134, bottom: 1134, left: 1134, right: 1134 } } },
      footers: { default: new Footer({ children: [new Paragraph({
        tabStops: [{ type: TabStopType.RIGHT, position: CONTENT_W }],
        border: { top: { style: BorderStyle.SINGLE, size: 4, color: "BFC5CC", space: 4 } },
        children: [...runs(c.footer, { size: 16, color: "777777" }), new TextRun({ text: "\t", size: 16 }),
          new TextRun({ children: [PageNumber.CURRENT], size: 16, color: "777777", font: c.font })],
      })] }) },
      children,
    }],
  });
}

// docx-js가 책갈피마다 같은 w:id를 쓴다 - 책갈피는 겹치지 않고 순서대로 열고 닫히므로 시작·끝 쌍에 번호를 다시 매긴다
async function fixBookmarkIds(buf) {
  const JSZip = require("jszip");
  const zip = await JSZip.loadAsync(buf);
  let xml = await zip.file("word/document.xml").async("string");
  let n = 0;
  xml = xml.replace(/<w:bookmark(Start|End)\b([^>]*?)w:id="\d+"/g, (m, kind, mid) => {
    if (kind === "Start") n += 1;
    return `<w:bookmark${kind}${mid}w:id="${n}"`;
  });
  zip.file("word/document.xml", xml);
  return zip.generateAsync({ type: "nodebuffer", compression: "DEFLATE" });
}

(async () => {
  for (const f of ["content_ko.js", "content_en.js", "content_zh.js"]) {
    const c = require(path.join(__dirname, f));
    const buf = await fixBookmarkIds(await Packer.toBuffer(build(c)));
    fs.writeFileSync(path.join(OUT, c.file), buf);
    console.log(c.file, buf.length);
  }
})();
