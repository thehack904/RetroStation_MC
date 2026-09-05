#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import io
import json
import queue
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

BG=(7,18,74); HEADER=(20,58,190); HEADER2=(14,42,170); PANEL=(10,31,105); BORDER=(70,101,210)
WHITE=(242,246,255); GOLD=(255,211,34); RED=(178,25,25); MUTED=(167,184,226); BLACK=(4,8,28)
IMAGE_BG=(8,24,86)
MAX_IMAGE_BYTES=8 * 1024 * 1024
IMAGE_TIMEOUT=(3.0, 6.0)

FRAME_DEADLINE_EPSILON = 1e-9

def _advance_deadline(next_due: float, interval: float, now: float) -> float:
    """Use the same real-time frame deadline behavior as Guide/Weather."""
    candidate = next_due + interval
    if candidate <= now:
        overdue = now - candidate
        remainder = overdue % interval
        if (
            abs(remainder) < FRAME_DEADLINE_EPSILON
            or abs(interval - remainder) < FRAME_DEADLINE_EPSILON
        ):
            return now + interval
        return now + (interval - remainder)
    return candidate


def font(size:int, bold:bool=False):
    candidates = ['/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
                  '/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf']
    for path in candidates:
        try: return ImageFont.truetype(path,size)
        except OSError: pass
    return ImageFont.load_default()


def fit(draw, text, max_width, start, minimum=12, bold=False):
    for s in range(start, minimum-1, -1):
        f=font(s,bold)
        if draw.textbbox((0,0),text,font=f)[2] <= max_width: return f
    return font(minimum,bold)


def wrap(draw,text,f,max_width,max_lines=3):
    words=str(text or '').split(); lines=[]; line=''
    for word in words:
        cand=(line+' '+word).strip()
        if draw.textbbox((0,0),cand,font=f)[2] <= max_width: line=cand
        else:
            if line: lines.append(line)
            line=word
            if len(lines)>=max_lines: break
    if line and len(lines)<max_lines: lines.append(line)
    if len(lines)==max_lines and words and ' '.join(lines) != ' '.join(words):
        while lines[-1] and draw.textbbox((0,0),lines[-1]+'…',font=f)[2] > max_width: lines[-1]=lines[-1][:-1]
        lines[-1]=lines[-1].rstrip()+'…'
    return lines


def load_state(path):
    try: return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception: return {'configured':False,'feed_count':0,'headlines':[],'updated':''}


class ImageCache:
    """Non-blocking image cache for feed artwork.

    Rendering never waits on the network.  Missing images are queued to a
    background worker and become visible automatically on a later frame.
    """
    def __init__(self, cache_dir: Path):
        self.cache_dir=cache_dir; self.cache_dir.mkdir(parents=True,exist_ok=True)
        self._mem:dict[str,Image.Image|None]={}
        self._pending:set[str]=set(); self._lock=threading.Lock(); self._q:queue.Queue[str]=queue.Queue()
        self._worker=threading.Thread(target=self._run,daemon=True,name='news-image-cache'); self._worker.start()

    def _path(self,url:str)->Path:
        return self.cache_dir/(hashlib.sha256(url.encode('utf-8','ignore')).hexdigest()+'.img')

    def request(self,url:str)->Image.Image|None:
        url=str(url or '').strip()
        if not url or urlparse(url).scheme not in {'http','https'}: return None
        with self._lock:
            if url in self._mem:
                im=self._mem[url]
                return im.copy() if im is not None else None
        disk=self._path(url)
        if disk.exists():
            try:
                with Image.open(disk) as src:
                    im=ImageOps.exif_transpose(src).convert('RGB')
                with self._lock: self._mem[url]=im
                return im.copy()
            except Exception:
                try: disk.unlink()
                except OSError: pass
        with self._lock:
            if url not in self._pending:
                self._pending.add(url); self._q.put(url)
        return None

    def _run(self):
        while True:
            url=self._q.get()
            im=None
            try:
                with requests.get(url,timeout=IMAGE_TIMEOUT,stream=True,headers={'User-Agent':'RetroStation-MC/1.4 NewsRenderer'}) as r:
                    r.raise_for_status(); ctype=(r.headers.get('content-type') or '').lower()
                    if ctype and not ctype.startswith('image/'):
                        raise ValueError('not an image response')
                    data=bytearray()
                    for chunk in r.iter_content(64*1024):
                        if not chunk: continue
                        data.extend(chunk)
                        if len(data)>MAX_IMAGE_BYTES: raise ValueError('image too large')
                with Image.open(io.BytesIO(bytes(data))) as src:
                    im=ImageOps.exif_transpose(src).convert('RGB')
                    im.thumbnail((1920,1080),Image.Resampling.LANCZOS)
                try: self._path(url).write_bytes(bytes(data))
                except OSError: pass
            except Exception:
                im=None
            with self._lock:
                self._mem[url]=im; self._pending.discard(url)
            self._q.task_done()


def draw_globe(d,cx,cy,r,color,width=3):
    d.ellipse((cx-r,cy-r,cx+r,cy+r),outline=color,width=width)
    d.ellipse((cx-r//2,cy-r,cx+r//2,cy+r),outline=color,width=max(1,width-1))
    d.line((cx-r,cy,cx+r,cy),fill=color,width=width)
    d.arc((cx-r,cy-r//2,cx+r,cy+r//2),0,360,fill=color,width=max(1,width-1))


def cover(im:Image.Image, size:tuple[int,int])->Image.Image:
    return ImageOps.fit(im,size,method=Image.Resampling.LANCZOS,centering=(0.5,0.5))


def placeholder(size:tuple[int,int])->Image.Image:
    im=Image.new('RGB',size,IMAGE_BG); d=ImageDraw.Draw(im)
    w,h=size
    # understated broadcast placeholder rather than a loud "No Image" card
    d.line((0,h-1,w,h-1),fill=BORDER,width=max(1,h//80))
    return im


def display_time(state, epoch):
    dt=datetime.fromtimestamp(epoch,tz=timezone.utc)
    if str(state.get('timezone','local')).strip().lower()=='utc':
        return dt.astimezone(timezone.utc)
    browser_timezone=str(state.get('browser_timezone','')).strip()
    if browser_timezone:
        try: return dt.astimezone(ZoneInfo(browser_timezone))
        except Exception: pass
    return dt.astimezone()

def render(state,w,h,now,images:ImageCache):
    im=Image.new('RGB',(w,h),BG); d=ImageDraw.Draw(im)
    sx=w/1280; sy=h/720; s=min(sx,sy)
    X=lambda x:int(x*sx); Y=lambda y:int(y*sy)
    def rect(coords,fill,outline=None,width=1):
        xy=(X(coords[0]),Y(coords[1]),X(coords[2]),Y(coords[3])); d.rectangle(xy,fill=fill,outline=outline,width=max(1,int(width*s)))
    def paste_story_image(url,coords):
        x0,y0,x1,y1=map(int,coords); ww=max(1,x1-x0); hh=max(1,y1-y0)
        src=images.request(url)
        art=cover(src,(ww,hh)) if src is not None else placeholder((ww,hh))
        im.paste(art,(x0,y0))

    # Header -- same proportions/content hierarchy as browser preview.
    rect((0,0,1280,92),HEADER)
    rect((0,89,1280,93),BORDER)
    draw_globe(d,X(43),Y(45),max(15,int(24*s)),(25,194,245),max(2,int(3*s)))
    brand_x=X(84); brand_y=Y(21)
    f1=font(max(20,int(36*s)),False); f2=font(max(20,int(36*s)),True)
    d.text((brand_x,brand_y),'RSMC ',font=f1,fill=WHITE)
    rsmc_w=d.textbbox((0,0),'RSMC ',font=f1)[2]
    d.text((brand_x+rsmc_w,brand_y),'NEWS NOW',font=f2,fill=WHITE)
    right='LATEST HEADLINES'; rf=font(max(18,int(30*s)),True); rw=d.textbbox((0,0),right,font=rf)[2]
    d.text((w-rw-X(28),Y(15)),right,font=rf,fill=WHITE)
    updated=state.get('updated','')
    try: updated_stamp=datetime.fromisoformat(updated.replace('Z','+00:00')).astimezone().strftime('%-I:%M %p')
    except Exception: updated_stamp=''
    clock=display_time(state,now).strftime('%I:%M:%S %p').lstrip('0')
    stamp=clock + (('   •   UPDATED: '+updated_stamp) if updated_stamp else '')
    uf=font(max(10,int(14*s)),True); uw=d.textbbox((0,0),stamp,font=uf)[2]
    d.text((w-uw-X(28),Y(55)),stamp,font=uf,fill=GOLD)

    configured=bool(state.get('configured',state.get('feed_count',0))); heads=state.get('headlines') or []
    body_top=Y(101); body_bottom=Y(646)

    if not configured or not heads:
        rect((22,108,1258,625),PANEL,BORDER,2)
        msg='NO RSS / ATOM FEEDS CONFIGURED' if not configured else 'NEWS FEED TEMPORARILY UNAVAILABLE'
        sub='Add up to six News feed URLs on the Virtual Channels page.' if not configured else 'RSMC will retry automatically.'
        mf=font(max(20,int(34*s)),True); mw=d.textbbox((0,0),msg,font=mf)[2]
        d.text(((w-mw)//2,Y(285)),msg,font=mf,fill=GOLD if not configured else WHITE)
        sf=font(max(12,int(17*s))); sw=d.textbbox((0,0),sub,font=sf)[2]
        d.text(((w-sw)//2,Y(340)),sub,font=sf,fill=MUTED)
    else:
        # Browser-preview geometry: dominant left story plus five image-backed rows.
        left=(X(22),Y(108),X(907),Y(638)); right=(X(917),Y(108),X(1258),Y(638))
        d.rectangle(left,fill=PANEL,outline=BORDER,width=max(1,int(2*s)))
        top=heads[0]
        d.text((X(38),Y(117)),'TOP STORY',font=font(max(11,int(16*s)),True),fill=GOLD)
        # headline band dynamically sized to 1-3 lines, then image gets all remaining space.
        tf=font(max(16,int(22*s)),True); title_lines=wrap(d,str(top.get('title','')).upper(),tf,X(825),3)
        line_h=max(18,int((tf.size+4)*sy)); band_h=max(Y(52),line_h*len(title_lines)+Y(14))
        band_y=Y(141); d.rectangle((X(32),band_y,X(900),band_y+band_h),fill=(165,10,10))
        yy=band_y+Y(8)
        for line in title_lines:
            d.text((X(43),yy),line,font=tf,fill=WHITE); yy += line_h
        summary=str(top.get('summary') or '').strip(); summary_h=Y(42) if summary else 0
        image_top=band_y+band_h; image_bottom=Y(638)-summary_h
        paste_story_image(top.get('image',''),(X(32),image_top,X(900),image_bottom))
        if summary:
            d.rectangle((X(32),image_bottom,X(900),Y(638)),fill=(7,25,92))
            d.line((X(32),image_bottom,X(900),image_bottom),fill=BORDER,width=max(1,int(2*s)))
            sf=font(max(9,int(13*s)))
            lines=wrap(d,summary,sf,X(830),2)
            syy=image_bottom+Y(7)
            for line in lines:
                d.text((X(43),syy),line,font=sf,fill=WHITE); syy+=max(12,int((sf.size+3)*sy))

        side=heads[1:6]
        row_gap=Y(7); total_h=right[3]-right[1]; row_h=(total_h-row_gap*4)//5
        y=right[1]
        for idx in range(5):
            y1=y+row_h
            d.rectangle((right[0],y,right[2],y1),fill=PANEL,outline=BORDER,width=max(1,int(2*s)))
            if idx < len(side):
                item=side[idx]; image_w=min(X(132),max(X(94),int((right[2]-right[0])*.34)))
                paste_story_image(item.get('image',''),(right[0]+2,y+2,right[0]+image_w,y1-2))
                tx=right[0]+image_w+X(10); maxw=max(30,right[2]-tx-X(8))
                hf=font(max(10,int(14*s)),True); lines=wrap(d,item.get('title',''),hf,maxw,4)
                text_h=sum(max(12,int((hf.size+3)*sy)) for _ in lines)
                tyy=max(y+Y(8), y+(row_h-text_h)//2)
                for line in lines:
                    d.text((tx,tyy),line,font=hf,fill=WHITE); tyy+=max(12,int((hf.size+3)*sy))
            y=y1+row_gap

    # ticker
    y0=Y(650); d.rectangle((0,y0,w,h),fill=BLACK); label_w=X(198); d.rectangle((0,y0,label_w,h),fill=(11,18,54)); d.line((label_w,y0,label_w,h),fill=BORDER,width=max(1,int(2*s)))
    d.text((X(16),Y(670)),'BREAKING NEWS:',font=font(max(10,int(15*s)),True),fill=GOLD)
    ticker='   •   '.join(str(x.get('title','')) for x in heads[:10]) or ('No headlines available' if configured else 'Configure News feeds in Virtual Channels')
    tf=font(max(10,int(15*s)),True); area=max(1,w-label_w); tw=max(1,d.textbbox((0,0),ticker,font=tf)[2])
    ticker_canvas=Image.new('RGB',(area,max(1,h-y0)),BLACK); td=ImageDraw.Draw(ticker_canvas)
    # Match Weather's ticker motion exactly: one string enters from the right,
    # travels fully off the left, then restarts based on wall-clock time.
    speed=max(40,w*6//100)
    cycle=max(1,(area+tw)/speed)
    offset=(now%cycle)*speed
    tx=int(area-offset)
    td.text((tx,Y(20)),ticker,font=tf,fill=GOLD)
    im.paste(ticker_canvas,(label_w,y0))
    return im


def main() -> int:
    p=argparse.ArgumentParser()
    p.add_argument('--state',required=True)
    p.add_argument('--fps',type=float,default=15)
    p.add_argument('--resolution',default='1280x720')
    p.add_argument('--image-cache-dir',default='')
    a=p.parse_args()
    w,h=[int(x) for x in a.resolution.lower().split('x',1)]
    frame_interval=1/max(.1,a.fps)
    cache_dir=Path(a.image_cache_dir) if a.image_cache_dir else Path(a.state).resolve().parent/'news_image_cache'
    images=ImageCache(cache_dir)

    stop_event=threading.Event()
    latest_frame_lock=threading.Lock()
    shared={'frame':Image.new('RGB',(w,h),BLACK).tobytes(),'version':0}
    state={}
    last_mtime=-1.0
    exit_code=0

    def _set(frame_bytes:bytes,version:int)->None:
        with latest_frame_lock:
            shared['frame']=frame_bytes; shared['version']=version

    def _get()->tuple[bytes,int]:
        with latest_frame_lock:
            return shared['frame'],int(shared['version'])

    # Match Guide/Weather clock and frame pacing exactly: a monotonic frame
    # deadline is translated to wall-clock time, rendering and output run
    # independently, and missed render slots are skipped instead of replayed.
    def _render_loop()->None:
        nonlocal state,last_mtime,exit_code
        mono_to_wall=time.time()-time.monotonic()
        next_due=time.monotonic()
        version=0
        consecutive_errors=0
        while not stop_event.is_set():
            sleep_for=next_due-time.monotonic()
            if sleep_for>0:
                stop_event.wait(sleep_for)
                if stop_event.is_set(): return
            epoch=next_due+mono_to_wall
            try:
                try:
                    mt=Path(a.state).stat().st_mtime
                    if mt!=last_mtime:
                        state=load_state(a.state); last_mtime=mt
                        for item in (state.get('headlines') or [])[:6]:
                            images.request(item.get('image',''))
                except OSError:
                    state={'configured':False,'headlines':[]}
                frame=render(state,w,h,epoch,images)
                if frame.size!=(w,h): frame=frame.resize((w,h))
                version+=1; _set(frame.tobytes(),version)
                consecutive_errors=0
            except Exception:
                consecutive_errors+=1
                traceback.print_exc(file=sys.stderr)
                if consecutive_errors>=10:
                    print('news_renderer: too many consecutive errors, exiting',file=sys.stderr)
                    exit_code=1; stop_event.set(); return
            next_due=_advance_deadline(next_due,frame_interval,time.monotonic())

    def _output_loop()->None:
        next_due=time.monotonic()
        while not stop_event.is_set():
            sleep_for=next_due-time.monotonic()
            if sleep_for>0:
                stop_event.wait(sleep_for)
                if stop_event.is_set(): return
            try:
                frame_bytes,_=_get()
                sys.stdout.buffer.write(frame_bytes); sys.stdout.buffer.flush()
            except BrokenPipeError:
                stop_event.set(); return
            next_due=_advance_deadline(next_due,frame_interval,time.monotonic())

    render_thread=threading.Thread(target=_render_loop,name='news-render')
    output_thread=threading.Thread(target=_output_loop,name='news-output')
    render_thread.start(); output_thread.start()
    try:
        while render_thread.is_alive() and output_thread.is_alive():
            render_thread.join(timeout=.05); output_thread.join(timeout=.05)
    except KeyboardInterrupt:
        stop_event.set()
    finally:
        stop_event.set(); render_thread.join(); output_thread.join()
    return exit_code

if __name__=='__main__':
    raise SystemExit(main())
