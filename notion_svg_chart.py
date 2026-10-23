"""SVG combo chart refreshed into an existing Notion image block at every sync.

Works on the Notion Free plan: this is a native SVG image, not a database Chart
view. Two series (daily lesson starts and observed 7-day pace) plus target.
SVG bytes go directly to Notion's File Upload API; no third-party chart host.
"""
from datetime import datetime
from html import escape
from math import ceil
from urllib.request import Request, urlopen
import json
import re

NAME="wanikani-daily-combined.svg"
CAPTION="WK_DAILY_COMBO_CHART · regenerated automatically from WaniKani assignments"


def chart_svg(rows, now, tz):
    """Return UTF-8 SVG with compact readable axes and a 15/day target line."""
    if not rows:
        return None
    rows=sorted(rows,key=lambda r:r["Day"])
    width,height=1160,520
    left,right,top,bottom=76,1122,128,429
    dmax=max(float(r.get("Lessons Today") or 0) for r in rows)
    pmax=max(float(r.get("Recent Pace (7d)") or 0) for r in rows)
    target=max(float(r.get("Daily Target") or 0) for r in rows)
    ymax=max(10,ceil(max(dmax,pmax,target)*1.12/5)*5)
    def x(i):
        return (left+right)/2 if len(rows)==1 else left+(right-left)*i/(len(rows)-1)
    def y(val):
        return bottom-(bottom-top)*float(val)/ymax
    def points(key):
        return " ".join(("M" if i==0 else "L")+f"{x(i):.1f},{y(row.get(key) or 0):.1f}"
                        for i,row in enumerate(rows))
    out=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
         f'<rect width="{width}" height="{height}" rx="22" fill="#151b28"/>',
         '<text x="76" y="53" fill="#f3f6fc" font-family="Arial,sans-serif" font-weight="700" font-size="27">WaniKani · Daily Lessons vs 7-day Pace</text>',
         f'<text x="76" y="83" fill="#a7b3c5" font-family="Arial,sans-serif" font-size="16">'
         f'Asia/Ho_Chi_Minh · from {rows[0]["Day"].strftime("%d/%m/%Y")} · '
         f'synced {now.astimezone(__import__("zoneinfo").ZoneInfo(tz)).strftime("%d/%m/%Y %H:%M")}</text>']
    # Five grid stripes, not one for every lesson.
    for i in range(5):
        value=ymax*i/4
        pos=y(value)
        out.append(f'<line x1="{left}" y1="{pos:.1f}" x2="{right}" y2="{pos:.1f}" stroke="#344054" stroke-width="1"/>')
        out.append(f'<text x="{left-16}" y="{pos+5:.1f}" fill="#a7b3c5" font-family="Arial,sans-serif" font-size="16" text-anchor="end">{value:g}</text>')
    target_y=y(target)
    out.append(f'<line x1="{left}" y1="{target_y:.1f}" x2="{right}" y2="{target_y:.1f}" stroke="#f3bb69" stroke-width="2" stroke-dasharray="7,7" opacity="0.85"/>')
    for key,color in (("Lessons Today","#46d9b5"),("Recent Pace (7d)","#a6b4ff")):
        if len(rows)>1:
            out.append(f'<path d="{points(key)}" stroke="{color}" stroke-width="4" fill="none" stroke-linejoin="round" stroke-linecap="round"/>')
        for i,row in enumerate(rows):
            cx,cy=x(i),y(row.get(key) or 0)
            out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="6" fill="{color}" stroke="#151b28" stroke-width="2"/>')
    # Label at most 9 well-spaced dates to avoid overlaps.
    tick_step=max(1,ceil((len(rows)-1)/8))
    for i,row in enumerate(rows):
        if i%tick_step==0 or i==len(rows)-1:
            label=row["Day"].strftime("%d/%m")
            out.append(f'<text x="{x(i):.1f}" y="{bottom+30}" text-anchor="middle" font-family="Arial,sans-serif" font-size="14" fill="#bdc8d9">{escape(label)}</text>')
    out.extend([
        '<g font-family="Arial,sans-serif" font-size="16">',
        '<circle cx="86" cy="493" r="7" fill="#46d9b5"/><text x="105" y="499" fill="#e7edf9">Lessons Today</text>',
        '<circle cx="320" cy="493" r="7" fill="#a6b4ff"/><text x="339" y="499" fill="#e7edf9">Recent Pace (7d)</text>',
        '<line x1="582" x2="618" y1="493" y2="493" stroke="#f3bb69" stroke-width="2" stroke-dasharray="7,6"/>',
        f'<text x="630" y="499" fill="#e7edf9">Daily Target: {target:g} lessons</text>',
        '</g></svg>'
    ])
    svg="\n".join(out).encode("utf-8")
    if len(svg)>4_000_000:
        raise ValueError("N5 chart SVG is unexpectedly too large")
    return svg


def send_svg(notion,token,svg,filename=NAME):
    upload=notion("POST","/file_uploads",token,{
        "filename":filename,"content_type":"image/svg+xml"})
    file_id=upload["id"]
    if not re.fullmatch(r"[0-9a-fA-F-]{36}",file_id):
        raise ValueError("Invalid Notion file-upload identifier")
    boundary="n5-wanikani-chart-svg"
    header=(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
            "Content-Type: image/svg+xml\r\n\r\n").encode()
    data=header+svg+f"\r\n--{boundary}--\r\n".encode()
    request=Request(f"https://api.notion.com/v1/file_uploads/{file_id}/send",
                    data=data,method="POST",headers={
        "Authorization":"Bearer "+token,
        "Notion-Version":"2025-09-03",
        "Content-Type":f"multipart/form-data; boundary={boundary}",
        "Accept":"application/json"})
    with urlopen(request,timeout=45) as response:
        sent=json.loads(response.read().decode("utf-8"))
    if sent.get("status")!="uploaded":
        raise ValueError(f"Notion SVG upload status: {sent.get('status')}")
    return file_id


def list_children(notion,token,page_id):
    result=[]
    cursor=None
    while True:
        url=f"/blocks/{page_id}/children?page_size=100"
        if cursor:
            from urllib.parse import quote
            url+="&start_cursor="+quote(cursor,safe="")
        page=notion("GET",url,token)
        result.extend(page.get("results",[]))
        if not page.get("has_more"):
            return result
        cursor=page.get("next_cursor")
        if not cursor:
            raise ValueError("Notion pagination has_more without a next cursor")


def update_daily_chart(notion,token,page_id,rows,now,tz):
    svg=chart_svg(rows,now,tz)
    if svg is None:
        return False
    children=list_children(notion,token,page_id)
    charts=[]
    for block in children:
        if block.get("type")!="image":
            continue
        image=block["image"]
        caption="".join(t.get("plain_text",t.get("text",{}).get("content",""))
                        for t in image.get("caption",[]))
        remote=image.get("file") or image.get("external") or {}
        src=remote.get("url","")
        if "WK_DAILY_COMBO_CHART" in caption or "wanikani-daily-lessons-pace.svg" in src or NAME in src:
            charts.append(block)
    if len(charts)>1:
        raise ValueError("More than one tagged daily combo chart image")
    if not charts:
        # The initial image is expected on the page; if manually removed,
        # restore it as a new image at the end rather than corrupt another image.
        action="PATCH";path=f"/blocks/{page_id}/children"
    else:
        action="PATCH";path=f"/blocks/{charts[0]['id']}"
    file_id=send_svg(notion,token,svg)
    # PATCH block:image accepts only the editable media reference/caption.
    # In particular, Notion rejects an image.type field on PATCH even though
    # the same field is required when appending a brand-new image block.
    image={"file_upload":{"id":file_id},
           "caption":[{"type":"text","text":{"content":CAPTION}}]}
    payload=({"image":image} if charts else
             {"children":[{"object":"block","type":"image",
                           "image":{"type":"file_upload",**image}}]})
    notion(action,path,token,payload)
    return True
