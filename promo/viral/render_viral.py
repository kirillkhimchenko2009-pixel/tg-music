from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
import numpy as np
import wave, os, math, subprocess

W,H,FPS,N=1080,1920,30,450
OUT=os.path.dirname(os.path.abspath(__file__))
os.chdir(OUT)
FRAMES=os.path.join(OUT,'frames')
os.makedirs(FRAMES,exist_ok=True)
SRC=r'F:/TGMusicBot/frontend/src/assets/hero-wave.jpg'
FONT=r'C:/Windows/Fonts/arialbd.ttf'
BG='#0a0b10'; VIOLET='#7c5cff'; PINK='#ff5c8a'; GOLD='#ffd166'; WHITE='#f6f3ff'; GREEN='#72f5a1'
source=Image.open(SRC).convert('RGB')
# Prepare distinct portrait punch-in crops from the original hero art.
def make_crop(focus_x, focus_y, zoom=1.0):
    sw,sh=source.size
    scale=max(W/sw,H/sh)*zoom
    rw,rh=round(sw*scale),round(sh*scale)
    im=source.resize((rw,rh),Image.Resampling.LANCZOS)
    cx=int(focus_x*rw); cy=int(focus_y*rh)
    left=max(0,min(rw-W,cx-W//2)); top=max(0,min(rh-H,cy-H//2))
    im=im.crop((left,top,left+W,top+H))
    im=ImageEnhance.Brightness(im).enhance(.65)
    im=ImageEnhance.Color(im).enhance(1.15)
    return im
# Portrait cover's horizontal source window is narrow; these focal points frame eyes/face/alternate detail.
crops=[make_crop(.49,.52,1.0),make_crop(.55,.48,1.24),make_crop(.40,.58,1.22)]
for i,c in enumerate(crops,1): c.save(f'crop_{i}.jpg',quality=92)
font_cache={}
def font(size):
    if size not in font_cache: font_cache[size]=ImageFont.truetype(FONT,size)
    return font_cache[size]
def fit_font(draw,text,maxw,maxh,size=100,spacing=8,stroke=0):
    while size>18:
        f=font(size); box=draw.multiline_textbbox((0,0),text,font=f,spacing=spacing,stroke_width=stroke,align='center')
        if box[2]-box[0]<=maxw and box[3]-box[1]<=maxh: return f
        size-=2
    return font(size)
def center_text(draw,text,y,maxw,size,color=WHITE,stroke=8,spacing=10):
    f=fit_font(draw,text,maxw,500,size,spacing,stroke)
    b=draw.multiline_textbbox((0,0),text,font=f,spacing=spacing,stroke_width=stroke,align='center')
    x=(W-(b[2]-b[0]))//2-b[0]
    draw.multiline_text((x,y),text,font=f,fill=color,stroke_width=stroke,stroke_fill='#050509',spacing=spacing,align='center')
    return f

def rounded(draw,xy,r,fill,outline=None,width=1): draw.rounded_rectangle(xy,radius=r,fill=fill,outline=outline,width=width)
def glow_text(im,text,y,maxw,size,color,stroke=9):
    # Compact blurred neon halo behind a crisp outlined headline.
    layer=Image.new('RGBA',(W,H),(0,0,0,0)); d=ImageDraw.Draw(layer)
    f=fit_font(d,text,maxw,550,size,7,stroke)
    bb=d.multiline_textbbox((0,0),text,font=f,spacing=7,stroke_width=stroke,align='center')
    x=(W-(bb[2]-bb[0]))//2-bb[0]
    d.multiline_text((x,y),text,font=f,fill=color,stroke_width=stroke+8,stroke_fill=color,spacing=7,align='center')
    halo=layer.filter(ImageFilter.GaussianBlur(24))
    im=Image.alpha_composite(im.convert('RGBA'),halo)
    d=ImageDraw.Draw(im)
    d.multiline_text((x,y),text,font=f,fill=color,stroke_width=stroke,stroke_fill='#050509',spacing=7,align='center')
    return im

def phone_ui(im,t):
    d=ImageDraw.Draw(im)
    # subtle interface plate
    rounded(d,(112,325,968,1510),52,(8,10,18,238),outline=(124,92,255,245),width=5)
    rounded(d,(137,350,943,1485),37,(18,19,31,255),outline=(255,255,255,30),width=2)
    rounded(d,(444,369,636,388),10,(73,74,91,255))
    d.text((178,425),'VIBE MUSIC',font=font(38),fill=WHITE,stroke_width=1,stroke_fill='#080910')
    d.text((180,494),'НАЙТИ ТРЕК',font=font(25),fill=(163,155,196))
    # active typing / completed search
    rounded(d,(166,546,914,644),25,(35,34,51,255),outline=(124,92,255,255),width=3)
    title='Кино — Пачка сигарет'
    progress=min(1,max(0,(t-2.78)/.80))
    shown=title[:int(len(title)*progress)]
    d.text((197,573),shown,font=font(31),fill=WHITE)
    if progress<1 and int(t*12)%2==0:
        x=197+d.textbbox((0,0),shown,font=font(31))[2]
        d.rectangle((x+4,578,x+8,613),fill=GOLD)
    # result card
    rounded(d,(164,690,916,995),28,(28,28,43,255),outline=(255,255,255,35),width=2)
    rounded(d,(190,720,348,878),20,(124,92,255,255))
    # abstract record / soundwave shapes, no emoji
    d.ellipse((222,752,316,846),outline=(248,245,255,230),width=4)
    d.ellipse((254,784,284,814),fill=(255,92,138,255))
    d.arc((208,738,330,860),25,300,fill=(255,209,102,255),width=3)
    d.text((376,724),'КИНО',font=font(29),fill=(174,167,202))
    d.text((376,770),'Пачка сигарет',font=font(34),fill=WHITE)
    d.text((376,828),'Виктор Цой',font=font(25),fill=(166,160,186))
    # download chip appears after typing
    rounded(d,(190,1043,888,1134),28,(34,67,51,255),outline=(114,245,161,255),width=3)
    d.ellipse((214,1065,257,1108),fill=GREEN)
    d.line((225,1086,234,1095,248,1076),fill=(7,25,15),width=5)
    d.text((278,1065),'Пачка сигарет · FLAC',font=font(29),fill=(221,255,230))
    d.text((196,1201),'БЕЗ ОЖИДАНИЯ',font=font(22),fill=(163,155,196))
    # animated progress bar / sound bars
    rounded(d,(194,1253,886,1265),6,(54,52,74,255))
    rounded(d,(194,1253,194+int(692*min(1,max(0,(t-3)/4))),1265),6,VIOLET)
    for i in range(28):
        x=207+i*24; barh=14+int((8+8*math.sin(i*1.4+t*8))*abs(math.sin(i*2.3+t*3)))
        d.rounded_rectangle((x,1370-barh,x+11,1370+barh),radius=5,fill=(124,92,255,220) if i%3 else (255,92,138,230))
    d.text((194,1420),'БЕСПЛАТНЫЙ ПОИСК • МОМЕНТАЛЬНО',font=font(18),fill=(141,133,165))
    # countdown timer safely in upper-right quadrant
    sec=max(0,5-int(max(0,t-2.65)/1.22))
    rounded(d,(790,178,978,370),34,(14,13,26,245),outline=(255,209,102,255),width=5)
    d.text((820,191),'ОСТАЛОСЬ',font=font(19),fill=(195,187,217))
    tf=font(112); d.text((846,220),str(sec),font=tf,fill=GOLD,stroke_width=5,stroke_fill='#15101a')
    if t>=8.62:
        # angled receipt-style zero price stamp
        stamp=Image.new('RGBA',(590,175),(0,0,0,0)); sd=ImageDraw.Draw(stamp)
        sd.rounded_rectangle((7,7,583,168),radius=13,outline=(255,53,72,255),width=8)
        sd.text((28,49),'ОПЛАТА: 0.00 ₽',font=font(53),fill=(255,76,89,255),stroke_width=1,stroke_fill=(50,0,0))
        stamp=stamp.rotate(7,resample=Image.Resampling.BICUBIC,expand=True)
        im.alpha_composite(stamp,(W//2-stamp.width//2,1510))

def draw_frame(i):
    t=i/FPS
    shake=(round(8*math.sin(i*7.7)),round(8*math.cos(i*8.3))) if i%12<3 or (t<1.4 and int(t*2.5)%2==0) or t>=13 else (0,0)
    zoom=1.0+(.08*max(0,1-((i%12)/3)) if i%12<3 else 0)
    # scene and background selection
    if t<1.4: scene=0; base=crops[1]
    elif t<2.6: scene=1; base=crops[0]
    elif t<10: scene=2; base=crops[0]
    elif t<11: scene=3; base=crops[0]
    elif t<12: scene=4; base=crops[1]
    elif t<13: scene=5; base=crops[2]
    else: scene=6; base=crops[0]
    if zoom!=1.0:
        z=base.resize((int(W*zoom),int(H*zoom)),Image.Resampling.BICUBIC)
        x=(z.width-W)//2; y=(z.height-H)//2
        base=z.crop((x,y,x+W,y+H))
    im=base.convert('RGBA')
    # dark gradient/vignette preserves legibility while leaving art visible.
    shade=Image.new('RGBA',(W,H),(0,0,0,86)); im=Image.alpha_composite(im,shade)
    d=ImageDraw.Draw(im)
    # stylized colored edge streaks and subtle top/bottom vignette bands
    for k in range(5):
        y=260+k*340+int(22*math.sin(t*7+k))
        d.line((0,y,1080,y-110),fill=(124,92,255,25),width=2)
    if scene==0:
        im=glow_text(im,'ТЫ ПЕРЕПЛАЧИВАЕШЬ\nЗА МУЗЫКУ',650,1000,108,GOLD,10)
        d=ImageDraw.Draw(im)
        rounded(d,(350,574,730,628),25,(10,11,16,210),outline=(255,209,102,180),width=2)
        d.text((408,584),'ЭТО СЕРЬЁЗНО?',font=font(24),fill=(255,235,189))
    elif scene==1:
        # price card with bold red strike
        rounded(d,(177,650,903,1020),38,(12,12,21,236),outline=(255,92,138,230),width=4)
        d.text((250,710),'199 ₽/мес',font=font(91),fill=(245,240,250),stroke_width=5,stroke_fill='#050509')
        d.line((239,815,845,700),fill=(255,48,66),width=20)
        d.line((239,815,845,700),fill=(255,160,160),width=5)
        im=glow_text(im,'А МОЖНО\nБЕСПЛАТНО',1060,890,112,WHITE,10)
    elif scene==2:
        phone_ui(im,t)
    elif scene in (3,4,5):
        messages={3:'FLAC\nБЕСПЛАТНО',4:'ЦЕЛЫЙ ПЛЕЙЛИСТ\nОДНИМ СООБЩЕНИЕМ',5:'18+\nБЕЗ БЛЮРА'}
        im=glow_text(im,messages[scene],770,980,112,[GREEN,GOLD,PINK][scene-3],10)
        d=ImageDraw.Draw(im)
        rounded(d,(327,680,753,733),24,(10,11,16,210),outline=(255,255,255,110),width=2)
        d.text((375,691),'VIBE MUSIC • ПРОСТО',font=font(22),fill=WHITE)
    else:
        # drop / decisive CTA
        pulse=1+0.012*math.sin((t-13)*2*math.pi*2)
        im=glow_text(im,'VIBE MUSIC',635,1000,int(120*pulse),WHITE,12)
        d=ImageDraw.Draw(im)
        rounded(d,(133,1005,947,1173),46,(124,92,255,255),outline=(255,209,102,255),width=5)
        d.rounded_rectangle((143,1015,937,1163),radius=40,outline=(255,92,138,255),width=4)
        txt='ССЫЛКА В ЗАКРЕПЕ'; f=fit_font(d,txt,750,100,44,8,2)
        b=d.textbbox((0,0),txt,font=f,stroke_width=2); d.text(((W-(b[2]-b[0]))//2,1055),txt,font=f,fill=WHITE,stroke_width=2,stroke_fill='#26155c')
        d.text((390,1265),'МУЗЫКА — ТВОЯ. ОПЛАТА — НЕТ.',font=font(23),fill=(224,218,241))
    # small @vibe watermark for first ten seconds only
    if t<10:
        d=ImageDraw.Draw(im); d.text((925,1570),'@vibe',font=font(23),fill=(255,255,255,165),stroke_width=2,stroke_fill=(0,0,0,170))
    # white 2-frame transition flashes, plus hook flash around 0.4 s
    flash=(int(round(.4*FPS))<=i<int(round(.4*FPS))+2) or any(i in (int(round(x*FPS)),int(round(x*FPS))+1) for x in (1.4,2.6,10,11,12,13))
    if flash:
        im=Image.blend(im,Image.new('RGBA',(W,H),(255,255,255,255)),.78)
    # shake is composited against black; retain full vertical frame.
    dx,dy=shake
    if dx or dy:
        shifted=Image.new('RGBA',(W,H),(7,8,12,255)); shifted.alpha_composite(im,(dx,dy)); im=shifted
    # final half-second fade to black
    if t>=14.5: im=Image.blend(im,Image.new('RGBA',(W,H),(0,0,0,255)),min(1,(t-14.5)/.5))
    return im.convert('RGB')

# Create 450 full-resolution PNG frames.
for i in range(N):
    draw_frame(i).save(os.path.join(FRAMES,f'{i:05d}.png'),compress_level=2)
    if i%75==0: print(f'frames: {i}/{N}',flush=True)

# 150 BPM mono phonk bed, synthesised deterministically at 44.1 kHz.
sr=44100; duration=15.; ns=int(sr*duration); audio=np.zeros(ns,dtype=np.float64)
rng=np.random.default_rng(41)
def add_event(start,signal):
    a=max(0,int(start*sr)); b=min(ns,a+len(signal))
    if b>a: audio[a:b]+=signal[:b-a]
def envtone(freq,seconds,amp,decay,phase=0):
    n=max(1,int(seconds*sr)); x=np.arange(n)/sr
    return amp*np.sin(2*np.pi*freq*x+phase)*np.exp(-x*decay)
beat=60/150
# Heavy four-on-the-floor kick with low sine and a short click transient.
for k in range(math.ceil(duration/beat)):
    st=k*beat
    n=int(.24*sr); x=np.arange(n)/sr
    f=52-18*np.minimum(x/.15,1)
    kick=.92*np.sin(2*np.pi*(52*x-9*x*x/.15))*np.exp(-x*19)
    click=.22*rng.normal(size=n)*np.exp(-x*90)
    add_event(st,kick+click)
    # distorted sub bass sustain on beats, alternating pitch slides.
    n=int(.35*sr); x=np.arange(n)/sr
    startf=80 if k%4 in (0,2) else 73
    phase=2*np.pi*(startf*x-20*x*x/.35)
    bass=.74*np.tanh(2.4*np.sin(phase))*np.exp(-x*2.8)
    add_event(st,bass)
# Metallic A-minor cowbell motif (A3/C4/E4/G4), alternating half-bars.
notes=[220.00,261.63,329.63,392.00,329.63,261.63,220.00,392.00]
for j in range(int(duration/(beat/2))):
    st=j*beat/2
    f=notes[(j//2+j%2*2)%len(notes)]
    n=int(.19*sr); x=np.arange(n)/sr
    bell=(np.sin(2*np.pi*f*x)+.72*np.sin(2*np.pi*(f*1.487)*x+.15)+.38*np.sin(2*np.pi*(f*2.237)*x+.4))
    bell*=np.exp(-x*20)*(.17 if j%2 else .24)
    add_event(st,bell)
# Bright 16th-note hats, stronger on the offbeats.
for j in range(int(duration/(beat/4))):
    st=j*beat/4
    n=int(.055*sr); x=np.arange(n)/sr
    noise=rng.normal(0,1,n); hp=np.concatenate(([noise[0]],noise[1:]-noise[:-1]))
    gain=.075 if j%4 else .13
    add_event(st,hp*gain*np.exp(-x*72))
# Filtered white-noise riser in the 1.5 s leading into the 13s drop.
n=int(1.5*sr); x=np.arange(n)/sr; noise=rng.normal(0,1,n)
hp=np.concatenate(([noise[0]],noise[1:]-noise[:-1]))
riser=hp*(x/1.5)**1.7*.23
add_event(11.5,riser)
# Impact at exactly 13 s: sub-bomb plus short noisy tail.
n=int(.95*sr); x=np.arange(n)/sr
impact=.95*np.sin(2*np.pi*(48*x-15*x*x))*np.exp(-x*4.4)
impact+=rng.normal(0,1,n)*np.exp(-x*13)*.24
add_event(13.,impact)
# Overall gentle saturation, peak-normalised without clipping.
audio=np.tanh(audio*.30)
peak=np.max(np.abs(audio))
if peak: audio=audio*(.75/peak)
pcm=np.int16(np.clip(audio,-1,1)*32767)
with wave.open(os.path.join(OUT,'beat_phonk.wav'),'wb') as wf:
    wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr); wf.writeframes(pcm.tobytes())
print('audio: 15s mono 44.1kHz',flush=True)
# Assemble using the requested codecs/settings.
cmd=['ffmpeg','-y','-hide_banner','-loglevel','warning','-framerate','30','-i','frames/%05d.png','-i','beat_phonk.wav','-c:v','libx264','-preset','medium','-crf','19','-pix_fmt','yuv420p','-c:a','aac','-b:a','160k','-shortest','shorts_viral_15s.mp4']
subprocess.run(cmd,check=True)
# Requested representative rendered previews.
for sec in (.5,5,11.5,13.5):
    frame=draw_frame(int(sec*FPS))
    frame.save(f'preview_{sec:g}s.png',compress_level=2)
print('video and preview images complete',flush=True)
# Delete only the generated frame sequence after successful encode.
import shutil
shutil.rmtree(FRAMES)
print('removed generated frames/',flush=True)
