"""Dark dual-axis WaniKani Lessons vs completed Reviews proxy chart in Notion.

Lessons are exact counts by assignment.started_at. Reviews are near-midnight
checkpoint deltas of cumulative meaning_correct, NOT exact daily sessions.
Two independent labeled Y axes prevent reviews from hiding lessons. No fake
zero is plotted on days without adequate checkpoint coverage.
"""
from datetime import datetime
from html import escape
from math import ceil
from zoneinfo import ZoneInfo
from notion_svg_chart import send_svg, list_children

FILENAME="wanikani-daily-lessons-reviews.svg"
TAG="WK_DAILY_LESSONS_REVIEWS_CHART"
CAPTION=TAG+" · Reviews ≈ meaning-correct delta near local midnight, not exact daily sessions"


def discipline_svg(rows, now, tz):
    if not rows:
        return None
    rows=sorted(rows,key=lambda r:r["Day"])
    w,h=1160,540
    left,right,top,bottom=87,1070,141,440
    target=max(float(r.get("Daily Target") or 0) for r in rows)
    lesson_max=max(float(r.get("Lessons Today") or 0) for r in rows)
    valid=[float(r["Reviews Done Approx"]) for r in rows
           if r.get("Reviews Done Approx") is not None]
    lmax=max(10,ceil(max(target,lesson_max,1)*1.16/5)*5)
    rmax=max(20,ceil(max(valid+[1])*1.16/20)*20)
    def x(i):
        return (left+right)/2 if len(rows)==1 else left+(right-left)*i/(len(rows)-1)
    def y(value,maximum):
        return bottom-(bottom-top)*float(value)/maximum
    def path_for(key,maximum):
        pieces=[]
        visible=False
        for i,row in enumerate(rows):
            value=row.get(key)
            if value is None:
                visible=False
                continue
            op="L" if visible else "M"
            pieces.append(op+f"{x(i):.1f},{y(value,maximum):.1f}")
            visible=True
        return " ".join(pieces)
    out=[
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">',
        f'<rect x="0" y="0" width="{w}" height="{h}" rx="22" fill="#151b28"/>',
        '<g font-family="Arial,sans-serif">',
        '<text x="87" y="54" fill="#f3f6fc" font-weight="700" font-size="27">WaniKani · Daily Lessons vs Reviews Done</text>',
        f'<text x="87" y="85" fill="#a7b3c5" font-size="16">'
        f'{escape(tz)} · from {rows[0]["Day"]:%d/%m/%Y} · synced '
        f'{now.astimezone(ZoneInfo(tz)):%d/%m/%Y %H:%M}</text>',
        '<text x="87" y="114" fill="#9eacbd" font-size="14">'
        'Reviews &#8776; meaning-correct checkpoint difference · incomplete/unknown days are labeled</text>',
        '<text x="87" y="131" fill="#46d9b5" font-size="13">Lessons (left axis)</text>',
        '<text x="1070" y="131" text-anchor="end" fill="#ffb46e" font-size="13">Reviews &#8776; (right axis)</text>',
    ]
    for i in range(5):
        lv=lmax*i/4
        rv=rmax*i/4
        pos=y(lv,lmax)
        out.extend([
            f'<line x1="{left}" y1="{pos:.1f}" x2="{right}" y2="{pos:.1f}" stroke="#344054" stroke-width="1"/>',
            f'<text x="{left-15}" y="{pos+5:.1f}" text-anchor="end" fill="#80d7c0" font-size="15">{lv:g}</text>',
            f'<text x="{right+15}" y="{pos+5:.1f}" text-anchor="start" fill="#ffba82" font-size="15">{rv:g}</text>',
        ])
    if target:
        yy=y(target,lmax)
        out.append(f'<line x1="{left}" y1="{yy:.1f}" x2="{right}" y2="{yy:.1f}" stroke="#f3bb69" stroke-width="2" stroke-dasharray="7,7" opacity="0.8"/>')
    for key,color,maximum in [
        ("Lessons Today","#46d9b5",lmax),
        ("Reviews Done Approx","#ff9e68",rmax),
    ]:
        points=path_for(key,maximum)
        if points and len(rows)>1:
            out.append(f'<path d="{points}" stroke="{color}" stroke-width="3.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/>')
        for i,row in enumerate(rows):
            value=row.get(key)
            if value is None:
                if key=="Reviews Done Approx":
                    out.append(f'<text x="{x(i):.1f}" y="{bottom-9}" fill="#ff9e68" font-size="17" text-anchor="middle">?</text>')
                continue
            out.append(f'<circle cx="{x(i):.1f}" cy="{y(value,maximum):.1f}" r="5.6" fill="{color}" stroke="#151b28" stroke-width="2"/>')
    step=max(1,ceil((len(rows)-1)/8))
    for i,row in enumerate(rows):
        if i%step==0 or i==len(rows)-1:
            label=escape(row["Day"].strftime("%d/%m"))
            out.append(f'<text x="{x(i):.1f}" y="{bottom+28}" text-anchor="middle" fill="#bdc8d9" font-size="14">{label}</text>')
    out.extend([
        '<circle cx="97" cy="501" r="7" fill="#46d9b5"/><text x="115" y="507" fill="#e7edf9" font-size="16">Lessons Today</text>',
        '<circle cx="310" cy="501" r="7" fill="#ff9e68"/><text x="328" y="507" fill="#e7edf9" font-size="16">Reviews Done (&#8776;)</text>',
        '<line x1="610" x2="648" y1="501" y2="501" stroke="#f3bb69" stroke-width="2" stroke-dasharray="7,6"/>',
        f'<text x="660" y="507" fill="#e7edf9" font-size="16">Lessons Target: {target:g}/day</text>',
        '</g></svg>'
    ])
    svg="\n".join(out).encode("utf-8")
    if len(svg)>4_000_000:
        raise ValueError("Daily reviews SVG is unexpectedly large")
    return svg


def _label(block):
    image=block.get("image",{})
    return "".join(t.get("plain_text",t.get("text",{}).get("content",""))
                   for t in image.get("caption",[]))


def update_discipline_chart(notion,token,page_id,rows,now,tz):
    svg=discipline_svg(rows,now,tz)
    if svg is None:
        return False
    children=list_children(notion,token,page_id)
    charts=[b for b in children if b.get("type")=="image" and TAG in _label(b)]
    if len(charts)>1:
        raise ValueError("Multiple daily Lessons vs Reviews charts found: refusing duplicates")
    file_id=send_svg(notion,token,svg,filename=FILENAME)
    image={"file_upload":{"id":file_id},
           "caption":[{"type":"text","text":{"content":CAPTION}}]}
    if charts:
        notion("PATCH",f"/blocks/{charts[0]['id']}",token,{"image":image})
        return True
    prior=[b for b in children if b.get("type")=="image" and
           "WK_DAILY_COMBO_CHART" in _label(b)]
    payload={"children":[
        {"object":"block","type":"heading_2","heading_2":{"rich_text":[
            {"type":"text","text":{"content":"🔁 Daily Lessons & Reviews · Theo dõi kỷ luật"}}]}},
        {"object":"block","type":"paragraph","paragraph":{"rich_text":[
            {"type":"text","text":{"content":(
              "Xanh ngọc: số lessons bắt đầu (chính xác); cam: số reviews hoàn thành ƯỚC TÍNH "
              "từ chênh lệch meaning_correct giữa các checkpoint gần 00:00 VN. "
              "Hai trục Y riêng; dấu ? là ngày thiếu dữ liệu, không phải 0. "
              "Ngày hiện tại chưa kết thúc; chỉ theo dõi hoạt động, không phải "
              "tỷ lệ hoàn thành số reviews đến hạn.")}}]}},
        {"object":"block","type":"image",
         "image":{"type":"file_upload",**image}}
    ]}
    # Keep the newly created chart beside the original, not at page bottom.
    if prior:
        payload["after"]=prior[0]["id"]
    notion("PATCH",f"/blocks/{page_id}/children",token,payload)
    return True
