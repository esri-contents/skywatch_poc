"""사용설명서 그림 준비: 결과 그림 축소 JPG, 표본·조사 예시, 리본 모의도(Pro 내장 아이콘). 크기는 dims.json.

  python prep_images.py <Add-in 실행 폴더(현장조사 내보내기를 한 것)>
  python prep_images.py            (리본 모의도만 다시 그림)
img/·dims.json은 저장소에 들어 있으므로 그림을 바꿀 때만 다시 돌린다. 아이콘(icons/*.png)은
ArcGIS.Desktop.Resources.dll에서 꺼낸 Pro 내장 이미지다(Imagery32, HtmlFile16 등).
"""
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

M = Path(__file__).parent
IMG = M / "img"
IMG.mkdir(exist_ok=True)
R = Path(__file__).resolve().parents[3] / "outputs" / "gwangmyeong_siheung_aerial" / "interpretation"
S = Path(sys.argv[1]) / "interpretation" if len(sys.argv) > 1 else None
dims = json.loads((M / "dims.json").read_text(encoding="utf-8")) if (M / "dims.json").exists() else {}


def save(src: Path, name: str, width: int = 1600):
    im = Image.open(src).convert("RGB")
    if im.width > width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    out = IMG / f"{name}.jpg"
    im.save(out, quality=85)
    dims[name] = [im.width, im.height]


if S is not None:
    save(R / "reports" / "figures" / "method_panels.png", "method")
    save(R / "reports" / "figures" / "overview_official.png", "overview", 1300)
    save(R / "reports" / "figures" / "distribution.png", "distribution", 1600)
    qa4 = sorted((R / "qa" / "round4").glob("unreg_*_000.jpg"))
    save(qa4[0], "qa_sample", 900)
    save(S / "survey" / "images" / "A01.jpg", "survey_new", 1200)
    save(S / "survey" / "images" / "U001.jpg", "survey_unreg", 900)

# 리본 모의도 - 버튼 이름은 Add-in 화면 그대로(한국어)
font = ImageFont.truetype(r"C:\Windows\Fonts\malgun.ttf", 15)
fsmall = ImageFont.truetype(r"C:\Windows\Fonts\malgun.ttf", 13)
fbold = ImageFont.truetype(r"C:\Windows\Fonts\malgunbd.ttf", 15)
W, H = 1180, 170
im = Image.new("RGB", (W, H), "#f3f3f3")
d = ImageDraw.Draw(im)
d.rectangle([0, 0, W, 34], fill="#e1e1e1")
tabs = ["프로젝트", "지도", "삽입", "분석", "보기", "편집", "이미지", "공유", "광명시흥 판독"]
x = 12
for t in tabs:
    f = fbold if t == "광명시흥 판독" else font
    w = d.textlength(t, font=f)
    if t == "광명시흥 판독":
        d.rectangle([x - 8, 4, x + w + 8, 35], fill="#f3f3f3", outline="#c8c8c8")
        d.text((x, 8), t, font=f, fill="#1f4e79")
    else:
        d.text((x, 8), t, font=f, fill="#444")
    x += w + 26
ic = M / "icons"


def big(x, icon, label):
    im.paste(Image.open(ic / icon).convert("RGBA").resize((40, 40)), (x + 18, 46), Image.open(ic / icon).convert("RGBA").resize((40, 40)))
    for i, part in enumerate(label.split("\n")):
        w = d.textlength(part, font=fsmall)
        d.text((x + 38 - w / 2, 92 + i * 17), part, font=fsmall, fill="#222")


def small(x, y, icon, label):
    ico = Image.open(ic / icon).convert("RGBA").resize((18, 18))
    im.paste(ico, (x, y), ico)
    d.text((x + 24, y - 1), label, font=fsmall, fill="#222")


big(12, "imagery32.png", "판독\n실행")
col = [("htmlfile16.png", "보고서 열기"), ("genericbuttonorange16.png", "보고서 다시 만들기"), ("addlayerfrompath16.png", "결과 레이어 추가")]
for i, (a, b) in enumerate(col):
    small(100, 46 + i * 28, a, b)
col = [("folderopenstate16.png", "결과 폴더"), ("inspectiontable16.png", "실행 이력")]
for i, (a, b) in enumerate(col):
    small(270, 46 + i * 28, a, b)
d.line([380, 42, 380, 150], fill="#c8c8c8")
d.text((150, 146), "필지 구조물 판독", font=fsmall, fill="#666")
big(392, "inspectionviewer32.png", "후보\n판독")
col = [("inspectiontable16.png", "표본 검수"), ("excelworkbookfile16.png", "현장조사 내보내기"), ("inspectionreport16.png", "현장조사 결과 가져오기")]
for i, (a, b) in enumerate(col):
    small(480, 46 + i * 28, a, b)
small(680, 46, "mapseries16.png", "필지 도면 PDF")
d.line([820, 42, 820, 150], fill="#c8c8c8")
d.text((560, 146), "검수·현장조사", font=fsmall, fill="#666")
im.save(IMG / "ribbon.png")
dims["ribbon"] = [W, H]
(M / "dims.json").write_text(json.dumps(dims), encoding="utf-8")
print(dims)
