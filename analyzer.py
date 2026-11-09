"""Local NOR/NPR desktop application. Videos never leave this computer."""
from pathlib import Path
import json
import math
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime

import cv2
import numpy as np
from PIL import Image, ImageTk
import batch_core as batch

HERE = Path(__file__).resolve().parent
REFERENCE = Path(r'C:\Users\zyuan14\Documents\NOR test')


def arena_geometry(shape, cx, cy, size, width, height):
    """size is circle radius or square side, in original video pixels."""
    half = size if shape == 'Circular' else size / 2
    if not all(math.isfinite(v) for v in (cx, cy, size)) or size <= 0:
        raise ValueError('Arena size must be positive and finite.')
    if cx - half < 0 or cy - half < 0 or cx + half >= width or cy + half >= height:
        raise ValueError('The arena must fit completely inside the video frame.')
    points = np.array([[cx-half, cy-half], [cx+half, cy-half],
                       [cx+half, cy+half], [cx-half, cy+half]], dtype=float)
    x, y = int(math.floor(cx-half)), int(math.floor(cy-half))
    right, bottom = int(math.ceil(cx+half)), int(math.ceil(cy+half))
    w, h = right-x+1, bottom-y+1
    yy, xx = np.mgrid[y:y+h, x:x+w]
    inside = ((xx-cx)**2 + (yy-cy)**2 <= size**2) if shape == 'Circular' else (
        (abs(xx-cx) <= half) & (abs(yy-cy) <= half))
    return points, (x, y, w, h), inside.astype(np.uint8)*255


def run_analysis(config_path):
    import scoring_engine as engine
    c = json.loads(Path(config_path).read_text(encoding='utf-8'))
    if c['session'] == 'test':
        calibration = batch.read_calibration(c['calibration'], c['animal'])
        if not math.isclose(float(calibration['common_object_radius_px']),c['object_radius']):
            raise ValueError('Test radius differs from the paired encoding calibration.')
        if not math.isclose(float(calibration.get('investigation_radius_multiplier',c['multiplier'])),c['multiplier']):
            raise ValueError('Test multiplier differs from the paired encoding calibration.')
    out = Path(c['output'])
    engine.OUTPUT_FOLDER = out
    engine.CALIBRATION_FOLDER = out
    engine.safe_filename_text = batch.safe_name
    engine.INVESTIGATION_RADIUS_MULTIPLIER = c['multiplier']
    engine.MAXIMUM_HEAD_ANGLE_DEGREES = c['angle']
    engine.MINIMUM_BOUT_DURATION_SECONDS = c['bout']
    engine.MAXIMUM_GAP_SECONDS = c['gap']
    engine.DIFFERENCE_THRESHOLD = c['threshold']
    engine.MINIMUM_MOUSE_AREA = c['min_area']
    engine.MAXIMUM_MOUSE_AREA = c['max_area']
    engine.IMMOBILITY_DURATION_SECONDS = float(c.get('immobility_seconds',10.))
    engine.IMMOBILITY_TOLERANCE_PX = float(c.get('immobility_tolerance',2.))
    engine.select_video_file = lambda: Path(c['video'])
    engine.ask_analysis_duration_minutes = lambda: c['minutes']
    engine.ask_session_type = lambda: c['session']
    engine.ask_preference_direction = lambda: c['positive']
    engine.ask_animal_id = lambda _: c['animal']
    engine.select_calibration_file = lambda: Path(c['calibration'])
    engine.load_calibration = lambda path: batch.read_calibration(path,c['animal'])
    engine.select_arena_polygon = lambda frame: batch.geometry(c,frame.shape[1],frame.shape[0])
    engine.select_object_circle = lambda frame, name: (
        np.array(c['objects'][0 if name == 'Object 1' else 1], dtype=float),
        c.get('encoding_radii',[c['object_radius']]*2)[0 if name == 'Object 1' else 1])

    def draw_arena(frame, points):
        color = (80, 220, 80)
        if c['shape'] == 'Circular':
            cv2.circle(frame, (round(c['cx']), round(c['cy'])), round(c['size']), color, 2)
        else:
            cv2.polylines(frame, [points.astype(np.int32)], True, color, 2)
    engine.draw_arena_polygon = draw_arena
    print(f"Two-object scoring | {c['shape']} arena | zone radius {c['object_radius']*c['multiplier']:.2f}px", flush=True)
    engine.main()
    # Append shape and protocol to the inherited summary; full geometry is in settings.json.
    for path in out.glob('*summary.csv'):
        table = engine.pd.read_csv(path)
        for key in ('shape', 'cx', 'cy', 'size'):
            table[key] = c[key]
        table['calibration_source'] = c.get('calibration_source',c.get('calibration',''))
        table.to_csv(path, index=False)
    (out / 'COMPLETE.txt').write_text('Analysis completed successfully.\n', encoding='utf-8')


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title('NOR / NPR Analyzer')
        self.root.geometry('1320x900')
        self.root.minsize(1050, 760)
        self.frame = None
        self.objects = [None, None]
        self.process = None
        self.events = queue.Queue()
        self.vars = {}
        self.extra = {}
        self.corner_draft = []
        self.drag_corner = None
        self.loading = False
        self.batch_window = None
        self.mode = tk.StringVar(value='Arena center')
        self.status = tk.StringVar(value='Open a video to begin.')
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('TButton', padding=6)
        style.configure('Title.TLabel', font=('Segoe UI', 20, 'bold'))
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill='both', expand=True)
        header = ttk.Frame(outer)
        header.pack(fill='x')
        ttk.Label(header, text='NOR / NPR Analyzer', style='Title.TLabel').pack(side='left')
        self.batch_button = ttk.Button(header,text='Batch & animal pairs…',command=self.show_batch)
        self.batch_button.pack(side='right')
        self.save_queue_button = ttk.Button(header,text='Save current setup to batch',command=self.save_to_batch)
        self.save_queue_button.pack(side='right',padx=8)
        ttk.Label(outer, text='Arena calibration  /  skeleton tracking  /  investigation & preference').pack(anchor='w', pady=(0,12))
        body = ttk.Frame(outer)
        body.pack(fill='both', expand=True)
        left = ttk.Frame(body, width=320)
        left.pack(side='left', fill='y', padx=(0,16))
        right = ttk.Frame(body)
        right.pack(side='left', fill='both', expand=True)
        self.controls = []
        for text, command in [('Open video…', self.open_video), ('Import metadata…', self.import_metadata),
                              ('Import encoding calibration…', self.import_calibration)]:
            b = ttk.Button(left, text=text, command=command)
            b.pack(fill='x', pady=2)
            self.controls.append(b)
        self.video_label = ttk.Label(left, text='No video selected', wraplength=290)
        self.video_label.pack(anchor='w', pady=5)
        fields = [('session','Session','encoding',['encoding','test']),
                  ('animal','Animal ID','animal',None),
                  ('positive','Novel / moved object','2',['1','2']),
                  ('minutes','Duration (minutes)','10',None),
                  ('shape','Arena shape','Circular',['Circular','Square']),
                  ('cx','Arena center X (px)','0',None), ('cy','Arena center Y (px)','0',None),
                  ('size','Arena radius / square side (px)','100',None),
                  ('object_radius','Common object radius (px)','12',None),
                  ('multiplier','Zone radius × object radius','4',None),
                  ('angle','Maximum head angle (degrees)','90',None),
                  ('bout','Minimum bout (seconds)','0.2',None),
                  ('gap','Maximum bridged gap (seconds)','0.2',None),
                  ('immobility_seconds','Exclude immobility > (s; 0=off)','10',None),
                  ('immobility_tolerance','Immobility motion tolerance (px)','2',None),
                  ('threshold','Background difference threshold','25',None),
                  ('min_area','Minimum mouse area (px²)','150',None),
                  ('max_area','Maximum mouse area (px²)','7000',None)]
        grid = ttk.Frame(left)
        grid.pack(fill='x')
        for row, (key,label,default,choices) in enumerate(fields):
            var = tk.StringVar(value=default)
            self.vars[key] = var
            ttk.Label(grid, text=label).grid(row=row,column=0,sticky='w',pady=3)
            widget = ttk.Combobox(grid,textvariable=var,values=choices,state='readonly',width=12) if choices else ttk.Entry(grid,textvariable=var,width=14)
            widget.grid(row=row,column=1,sticky='e',padx=(6,0))
            self.controls.append(widget)
            var.trace_add('write', lambda *_, key=key: self.changed(key))
        self.zone_label = ttk.Label(left, text='', wraplength=310)
        self.zone_label.pack(anchor='w',pady=8)
        ttk.Label(left,text='Square arena: use Arena corners for tilted views.\nCircle size = radius; square size = side.\nBatch pairs share the encoding object radius.\nUse object-edge tools to measure encoding radii.',wraplength=310).pack(anchor='w')
        cornerbar = ttk.Frame(right)
        cornerbar.pack(fill='x',pady=(0,4))
        b = ttk.Radiobutton(cornerbar,text='Arena corners (4 clicks / drag to adjust)',variable=self.mode,value='Arena corners')
        b.pack(side='left'); self.controls.append(b)
        for label,command in [('Redraw corners',self.reset_corners),('Undo corner',self.undo_corner)]:
            b=ttk.Button(cornerbar,text=label,command=command)
            b.pack(side='left',padx=4); self.controls.append(b)
        toolbar = ttk.Frame(right)
        toolbar.pack(fill='x')
        for mode in ('Arena center','Arena edge','Object 1','Object 2','Object 1 edge','Object 2 edge'):
            b = ttk.Radiobutton(toolbar,text=mode,variable=self.mode,value=mode)
            b.pack(side='left',padx=5)
            self.controls.append(b)
        ttk.Label(right,text='Click the frame to place the selected item. Edit sizes on the left; overlays update immediately.').pack(anchor='w',pady=6)
        self.canvas = tk.Canvas(right,bg='#18212b',highlightthickness=0)
        self.canvas.pack(fill='both',expand=True)
        self.canvas.bind('<Button-1>',self.click)
        self.canvas.bind('<B1-Motion>',self.drag_corner_motion)
        self.canvas.bind('<ButtonRelease-1>',self.release_corner)
        self.canvas.bind('<Configure>',lambda _: self.redraw())
        self.log = tk.Text(right,height=9,bg='#101923',fg='#c9dfec',font=('Consolas',9),state='disabled')
        self.log.pack(fill='x',pady=(8,0))
        bottom = ttk.Frame(outer)
        bottom.pack(fill='x',pady=(12,0))
        self.start_button = ttk.Button(bottom,text='Run analysis',command=self.start)
        self.start_button.pack(side='left')
        self.cancel_button = ttk.Button(bottom,text='Stop',command=self.cancel,state='disabled')
        self.cancel_button.pack(side='left',padx=8)
        ttk.Button(bottom,text='Open results folder',command=self.open_results).pack(side='left')
        ttk.Label(bottom,textvariable=self.status,wraplength=660).pack(side='left',padx=12)
        self.output = HERE / 'results'
        self.calibration = ''
        self.root.protocol('WM_DELETE_WINDOW',self.close)
        self.root.after(150,self.poll)

    def changed(self,key):
        if not self.loading:
            if key == 'session': self.vars['minutes'].set(str(batch.default_minutes(self.vars['session'].get())))
            if key in ('shape','cx','cy','size'):
                self.extra.pop('arena_points',None)
                self.corner_draft=[]; self.drag_corner=None
            if key == 'object_radius': self.extra.pop('encoding_radii',None)
        self.redraw()

    def show_batch(self):
        if self.batch_window is None or not self.batch_window.window.winfo_exists():
            from batch_ui import BatchWindow
            self.batch_window = BatchWindow(self)
        self.batch_window.window.deiconify(); self.batch_window.window.lift()

    def save_to_batch(self):
        try: c = self.configuration()
        except Exception as e: messagebox.showerror('Check setup',str(e)); return
        self.show_batch()
        try: self.batch_window.add(c)
        except ValueError as e: messagebox.showerror('Batch is running',str(e)); return
        self.status.set('Setup saved to batch. Encoding radius and multiplier will also apply to the paired test.')

    def load_configuration(self,c):
        self.open_video(c['video'])
        if self.frame is None or self.video != c['video']: raise ValueError('Video could not be loaded.')
        self.loading = True
        try:
            for key,var in self.vars.items():
                if key in c: var.set(str(c[key]))
                elif key in ('immobility_seconds','immobility_tolerance'): var.set(str(batch.DEFAULTS[key]))
            self.objects = c['objects']
            self.calibration = c.get('calibration','')
            self.extra = {k:c[k] for k in ('arena_points','encoding_radii','source_metadata') if k in c}
        finally: self.loading = False
        self.status.set('Edit this recording, then Save current setup to batch. Each session keeps its own object positions.')
        self.redraw()

    def open_video(self, path=None):
        path = path or filedialog.askopenfilename(initialdir=REFERENCE/'video',filetypes=[('Videos','*.mp4 *.avi *.mov *.mkv'),('All files','*.*')])
        if not path: return
        cap = cv2.VideoCapture(str(path))
        ok, frame = cap.read()
        fps, count = cap.get(cv2.CAP_PROP_FPS), cap.get(cv2.CAP_PROP_FRAME_COUNT)
        cap.release()
        if not ok or not math.isfinite(fps) or fps <= 0 or count < 1:
            messagebox.showerror('Video error','Cannot read this video or its frame rate.'); return
        self.video = str(Path(path).resolve())
        self.extra = {}
        self.corner_draft=[]; self.drag_corner=None
        self.calibration = ''
        self.frame, self.fps, self.count = frame, fps, count
        self.objects = [None,None]
        h,w = frame.shape[:2]
        for k,v in {'cx':w/2,'cy':h/2,'size':min(w,h)*0.4,'animal':Path(path).stem,'minutes':batch.default_minutes(self.vars['session'].get())}.items():
            self.vars[k].set(str(v))
        self.video_label.config(text=f'{Path(path).name}\n{w} × {h} · {fps:.3f} fps · {count/fps/60:.2f} min')
        self.status.set('Place arena center and edge, then both object centers.')
        self.redraw()

    def import_calibration(self):
        path = filedialog.askopenfilename(initialdir=REFERENCE/'calibration',filetypes=[('Calibration JSON','*.json')])
        if not path: return
        try:
            c = json.loads(Path(path).read_text(encoding='utf-8'))
            radius = float(c['common_object_radius_px'])
            if not math.isfinite(radius) or radius <= 0: raise ValueError('Invalid object radius')
            self.vars['object_radius'].set(str(radius))
            self.vars['animal'].set(c['animal_id'])
            self.vars['multiplier'].set(str(c.get('investigation_radius_multiplier',4)))
            self.calibration = path
            self.status.set('Encoding calibration imported. Radius remains editable.')
        except Exception as e: messagebox.showerror('Calibration error',str(e))

    def import_metadata(self):
        path = filedialog.askopenfilename(initialdir=REFERENCE/'metadata',filetypes=[('Metadata JSON','*.json')])
        if not path: return
        try:
            c = batch.from_metadata(path)
            if not Path(c['video']).is_file():
                video = filedialog.askopenfilename(title='Locate video for this metadata')
                if not video: return
                c['video'] = video
            self.load_configuration(c)
        except Exception as e: messagebox.showerror('Metadata error',str(e))

    def click(self,event):
        if self.frame is None or self.process or (self.batch_window and self.batch_window.running): return
        x,y = (event.x-self.ox)/self.scale,(event.y-self.oy)/self.scale
        h,w = self.frame.shape[:2]
        if not (0 <= x < w and 0 <= y < h): return
        mode = self.mode.get()
        if mode == 'Arena corners':
            if self.vars['shape'].get() != 'Square': self.vars['shape'].set('Square')
            points=self.extra.get('arena_points')
            if points:
                distances=[math.hypot(x-px,y-py)*self.scale for px,py in points]
                nearest=int(np.argmin(distances))
                if distances[nearest] <= 14:
                    self.drag_corner=nearest
                    self.corner_draft=[list(p) for p in points]
                    self.status.set('Drag the corner to the arena boundary, then release.')
                else: self.status.set('Drag a numbered corner, or choose Redraw corners to start over.')
            else:
                self.corner_draft.append([x,y])
                if len(self.corner_draft)==4: self.commit_corners()
                else: self.status.set(f'Corner {len(self.corner_draft)}/4 placed. Click the remaining arena corners.')
        elif mode == 'Arena center':
            self.vars['cx'].set(f'{x:.2f}'); self.vars['cy'].set(f'{y:.2f}')
        elif mode == 'Arena edge':
            try: dx,dy = x-float(self.vars['cx'].get()),y-float(self.vars['cy'].get())
            except ValueError:
                self.status.set('Enter a valid arena center before choosing the edge.'); return
            size = math.hypot(dx,dy) if self.vars['shape'].get() == 'Circular' else 2*max(abs(dx),abs(dy))
            self.vars['size'].set(f'{size:.2f}')
        elif mode.endswith('edge'):
            i = int(mode.split()[1])-1
            if self.objects[i] is None:
                self.status.set('Place the object center before selecting its edge.'); return
            radii = self.extra.get('encoding_radii',[float(self.vars['object_radius'].get())]*2).copy()
            radii[i] = math.hypot(x-self.objects[i][0],y-self.objects[i][1])
            self.vars['object_radius'].set(f'{sum(radii)/2:.8g}')
            self.extra['encoding_radii'] = radii
            self.status.set(f'Encoding radii: {radii[0]:.2f}, {radii[1]:.2f} px; common radius is their mean.')
        else: self.objects[int(mode[-1])-1] = [x,y]
        self.redraw()

    def reset_corners(self):
        if self.frame is None or self.process or (self.batch_window and self.batch_window.running): return
        self.vars['shape'].set('Square')
        self.extra.pop('arena_points',None)
        self.corner_draft=[]; self.drag_corner=None
        self.mode.set('Arena corners')
        self.status.set('Click the four arena corners. You can click them in any order.')
        self.redraw()

    def undo_corner(self):
        if self.process or (self.batch_window and self.batch_window.running): return
        if self.extra.get('arena_points'): self.corner_draft=[list(p) for p in self.extra.pop('arena_points')]
        if self.corner_draft: self.corner_draft.pop()
        self.drag_corner=None; self.mode.set('Arena corners')
        self.status.set(f'{len(self.corner_draft)}/4 corners placed. Click to replace the last corner.')
        self.redraw()

    def commit_corners(self):
        points=np.asarray(self.corner_draft,dtype=float)
        # Sort around their centroid so arbitrary click order does not produce crossed edges.
        center=points.mean(axis=0)
        points=points[np.argsort(np.arctan2(points[:,1]-center[1],points[:,0]-center[0]))]
        points=np.roll(points,-int(np.argmin(points.sum(axis=1))),axis=0)
        candidate=dict(shape='Square',arena_points=points.tolist())
        h,w=self.frame.shape[:2]
        try: batch.geometry(candidate,w,h)
        except ValueError:
            if self.extra.get('arena_points'):
                self.corner_draft=[]
                self.status.set('That corner position is invalid. The previous arena was kept.')
            else:
                self.status.set('Corners must enclose a convex arena. Click a replacement for the last corner.')
                self.corner_draft=self.corner_draft[:3]
            return
        self.extra['arena_points']=points.tolist()
        self.corner_draft=[]
        self.loading=True
        try:
            self.vars['cx'].set(f'{center[0]:.3f}')
            self.vars['cy'].set(f'{center[1]:.3f}')
            self.vars['size'].set(f'{max(np.ptp(points[:,0]),np.ptp(points[:,1])):.3f}')
        finally: self.loading=False
        self.status.set('Four-corner arena saved. Drag numbered corners to refine it; object positions are independent.')

    def drag_corner_motion(self,event):
        if self.drag_corner is None or self.process or (self.batch_window and self.batch_window.running): return
        h,w=self.frame.shape[:2]
        self.corner_draft[self.drag_corner]=[float(np.clip((event.x-self.ox)/self.scale,0,w-1)),
                                              float(np.clip((event.y-self.oy)/self.scale,0,h-1))]
        self.redraw()

    def release_corner(self,event):
        if self.drag_corner is None: return
        self.drag_corner=None
        self.commit_corners()
        self.redraw()

    def redraw(self):
        if self.frame is None or not hasattr(self,'canvas'): return
        h,w = self.frame.shape[:2]
        cw,ch = max(1,self.canvas.winfo_width()),max(1,self.canvas.winfo_height())
        self.scale = min(cw/w,ch/h)
        dw,dh = max(1,int(w*self.scale)),max(1,int(h*self.scale))
        self.ox,self.oy = (cw-dw)/2,(ch-dh)/2
        image = cv2.cvtColor(self.frame,cv2.COLOR_BGR2RGB)
        self.photo = ImageTk.PhotoImage(Image.fromarray(image).resize((dw,dh)))
        self.canvas.delete('all')
        self.canvas.create_image(self.ox,self.oy,image=self.photo,anchor='nw')
        try:
            cx,cy,size,r,m = [float(self.vars[k].get()) for k in ('cx','cy','size','object_radius','multiplier')]
            def box(x,y,r):
                return (self.ox+(x-r)*self.scale,self.oy+(y-r)*self.scale,self.ox+(x+r)*self.scale,self.oy+(y+r)*self.scale)
            if self.vars['shape'].get() == 'Circular': self.canvas.create_oval(*box(cx,cy,size),outline='#65efab',width=2)
            elif self.corner_draft or self.extra.get('arena_points'):
                points=self.corner_draft or self.extra['arena_points']
                coords = [value for px,py in points for value in (self.ox+px*self.scale,self.oy+py*self.scale)]
                if len(points)==4: self.canvas.create_polygon(*coords,outline='#65efab',fill='',width=2)
                elif len(points)>1: self.canvas.create_line(*coords,fill='#65efab',width=2)
                for i,(px,py) in enumerate(points):
                    sx,sy=self.ox+px*self.scale,self.oy+py*self.scale
                    self.canvas.create_oval(sx-6,sy-6,sx+6,sy+6,fill='#65efab',outline='#18212b')
                    self.canvas.create_text(sx+12,sy-12,text=str(i+1),fill='#65efab',font=('Segoe UI',12,'bold'))
            elif self.mode.get() != 'Arena corners': self.canvas.create_rectangle(*box(cx,cy,size/2),outline='#65efab',width=2)
            for i,point in enumerate(self.objects):
                if point is None: continue
                color = ['#ffe66b','#66dcff'][i]
                self.canvas.create_oval(*box(*point,r*m),outline=color,width=2,dash=(5,3))
                self.canvas.create_oval(*box(*point,r),outline=color,width=2)
                self.canvas.create_text(self.ox+point[0]*self.scale,self.oy+point[1]*self.scale,text=str(i+1),fill=color,font=('Segoe UI',13,'bold'))
            self.zone_label.config(text=f'Investigation-zone radius: {r*m:.2f} px\nMaximum nose-to-object-edge distance: {r*(m-1):.2f} px')
        except (ValueError,tk.TclError): pass

    def configuration(self):
        if self.frame is None: raise ValueError('Open a video first.')
        if self.corner_draft: raise ValueError('Finish the four-corner arena selection before saving or running.')
        if any(p is None for p in self.objects): raise ValueError('Place both object centers on the frame.')
        c = {k:v.get() for k,v in self.vars.items()}
        for k in ('minutes','cx','cy','size','object_radius','multiplier','angle','bout','gap','threshold','min_area','max_area','immobility_seconds','immobility_tolerance'):
            c[k] = float(c[k])
            if not math.isfinite(c[k]): raise ValueError(f'{k} must be finite.')
        for k in ('minutes','size','object_radius','min_area','max_area'):
            if c[k] <= 0: raise ValueError(f'{k} must be positive.')
        if c['multiplier'] <= 1: raise ValueError('Zone multiplier must exceed 1.')
        if not 0 <= c['angle'] <= 180: raise ValueError('Head angle must be between 0 and 180 degrees.')
        if c['bout'] < 0 or c['gap'] < 0: raise ValueError('Bout and gap durations cannot be negative.')
        if c['immobility_seconds'] < 0 or c['immobility_tolerance'] < 0: raise ValueError('Immobility settings cannot be negative.')
        if not 0 < c['threshold'] <= 255: raise ValueError('Difference threshold must be between 1 and 255.')
        if c['max_area'] <= c['min_area']: raise ValueError('Maximum mouse area must exceed minimum.')
        if not c['animal'].strip(): raise ValueError('Enter an animal ID.')
        h,w = self.frame.shape[:2]
        c.update(self.extra)
        if c.get('encoding_radii'): c['object_radius'] = sum(c['encoding_radii'])/2
        _,roi,mask = batch.geometry(c,w,h)
        for p in self.objects:
            x,y = int(round(p[0]))-roi[0],int(round(p[1]))-roi[1]
            if not (0 <= y < mask.shape[0] and 0 <= x < mask.shape[1] and mask[y,x]):
                raise ValueError('Both objects must lie inside the arena.')
        c.update(video=self.video,objects=self.objects,positive=int(c['positive']),calibration=self.calibration,ready=True)
        return c

    def start(self):
        try: c = self.configuration()
        except Exception as e: messagebox.showerror('Check settings',str(e)); return
        run = HERE/'results'/datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        run.mkdir(parents=True)
        self.output = run
        c['output'] = str(run)
        if c['session'] == 'test':
            try:
                data = batch.read_calibration(c['calibration'],c['animal'])
                c['object_radius'] = float(data['common_object_radius_px'])
                c['multiplier'] = float(data.get('investigation_radius_multiplier',c['multiplier']))
                c.pop('encoding_radii',None)
            except Exception as e:
                messagebox.showerror('Test needs encoding calibration',str(e)+'\nUse Batch & animal pairs to process encoding and test together.'); return
        config = run/'settings.json'
        config.write_text(json.dumps(c,indent=2),encoding='utf-8')
        try:
            self.process = subprocess.Popen([sys.executable,'-u',str(HERE/'analyzer.py'),'--run',str(config)],
                stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',
                creationflags=subprocess.CREATE_NO_WINDOW,env=os.environ.copy())
        except OSError as e:
            (run/'INCOMPLETE.txt').write_text(str(e),encoding='utf-8')
            messagebox.showerror('Could not start analysis',str(e)); return
        for widget in self.controls: widget.configure(state='disabled')
        self.start_button.configure(state='disabled'); self.cancel_button.configure(state='normal')
        self.status.set('Analyzing… progress appears below. Stop leaves partial results.')
        self.cancelled = False
        def read_output(process):
            with (run/'analysis.log').open('w',encoding='utf-8') as log:
                for line in process.stdout:
                    log.write(line); log.flush(); self.events.put(('log',line))
            self.events.put(('done',process.wait()))
        threading.Thread(target=read_output,args=(self.process,),daemon=True).start()

    def poll(self):
        try:
            while True:
                kind,value = self.events.get_nowait()
                if kind == 'log':
                    self.log.configure(state='normal'); self.log.insert('end',value); self.log.see('end'); self.log.configure(state='disabled')
                else:
                    self.process = None
                    for widget in self.controls:
                        widget.configure(state='readonly' if isinstance(widget,ttk.Combobox) else 'normal')
                    self.start_button.configure(state='normal'); self.cancel_button.configure(state='disabled')
                    state = 'Stopped; results are incomplete.' if self.cancelled else ('Complete. Results saved.' if value == 0 else 'Analysis failed. See analysis.log in results.')
                    self.status.set(state)
                    if value != 0: (self.output/'INCOMPLETE.txt').write_text(state,encoding='utf-8')
        except queue.Empty: pass
        self.root.after(150,self.poll)

    def cancel(self):
        if self.process:
            self.cancelled = True; self.process.terminate(); self.status.set('Stopping…')

    def open_results(self):
        self.output.mkdir(parents=True,exist_ok=True)
        os.startfile(str(self.output))

    def close(self):
        if self.batch_window and self.batch_window.running:
            if not messagebox.askyesno('Batch running','Stop the batch and close?'): return
            self.batch_window.stop()
        if self.process and not messagebox.askyesno('Analysis running','Stop the current analysis and close?'): return
        self.cancel(); self.root.destroy()


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--run': run_analysis(sys.argv[2])
    else: App().root.mainloop()
