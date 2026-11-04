from pathlib import Path
from copy import deepcopy
from datetime import datetime
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import batch_core as core

HERE = Path(__file__).resolve().parent


class BatchWindow:
    def __init__(self,app):
        self.app = app
        self.window = tk.Toplevel(app.root)
        self.window.title('Batch processing · animal encoding / test pairs')
        self.window.geometry('1180x650')
        self.items = []
        self.jobs = []
        self.process = None
        self.running = False
        self.run_token = 0
        self.events = queue.Queue()
        self.autosave = HERE/'batch_queue.json'
        self.state = tk.StringVar(value='Import metadata or add videos. Pairing uses the exact animal ID.')
        frame = ttk.Frame(self.window,padding=12)
        frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='Encoding → shared radius calibration → test',font=('Segoe UI',16,'bold')).pack(anchor='w')
        ttk.Label(frame,text='One scoring method for novel objects or locations. Choose the positive object per session.').pack(anchor='w',pady=(3,10))
        bar = ttk.Frame(frame); bar.pack(fill='x')
        self.buttons = []
        for label,command in [('Import metadata folder…',self.import_folder),('Add videos…',self.add_videos),
                              ('Load queue…',self.load_queue),('Save queue…',self.save_queue),
                              ('Configure selected',self.edit),('Remove selected',self.remove)]:
            b=ttk.Button(bar,text=label,command=command); b.pack(side='left',padx=2); self.buttons.append(b)
        columns = ('animal','session','video','shape','radius','zone','pair','state')
        table=ttk.Frame(frame)
        table.pack(fill='both',expand=True,pady=10)
        self.tree = ttk.Treeview(table,columns=columns,show='headings',selectmode='extended')
        for col,title,width in [('animal','Animal / visit ID',165),('session','Session',80),('video','Video',245),
                                ('shape','Arena',80),('radius','Common r (px)',95),('zone','Zone × r',75),
                                ('pair','Pair',110),('state','Status',170)]:
            self.tree.heading(col,text=title); self.tree.column(col,width=width,minwidth=50)
        self.tree.pack(side='left',fill='both',expand=True)
        scroll=ttk.Scrollbar(table,orient='vertical',command=self.tree.yview)
        scroll.pack(side='right',fill='y'); self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind('<Double-1>',lambda _: self.edit())
        ttk.Label(frame,text='Configure each new video, then use “Save current setup to batch” in the main window.\nEncoding radius and zone multiplier are shared automatically; test object centers remain independent.').pack(anchor='w')
        bottom = ttk.Frame(frame); bottom.pack(fill='x',pady=10)
        self.check = ttk.Button(bottom,text='Validate pairs',command=self.validate); self.check.pack(side='left'); self.buttons.append(self.check)
        self.run_button=ttk.Button(bottom,text='Run batch',command=self.start); self.run_button.pack(side='left',padx=6); self.buttons.append(self.run_button)
        self.stop_button=ttk.Button(bottom,text='Stop batch',command=self.stop,state='disabled'); self.stop_button.pack(side='left')
        ttk.Button(bottom,text='Open batch results',command=self.open_results).pack(side='left',padx=6)
        ttk.Label(frame,textvariable=self.state,wraplength=1120).pack(anchor='w')
        self.output = HERE/'results'
        self.window.protocol('WM_DELETE_WINDOW',self.hide)
        if self.autosave.exists():
            try: self.items=json.loads(self.autosave.read_text(encoding='utf-8'))
            except Exception: pass
        self.refresh()
        self.window.after(150,self.poll)

    def persist(self):
        temp=self.autosave.with_suffix('.tmp')
        temp.write_text(json.dumps(self.items,indent=2),encoding='utf-8'); temp.replace(self.autosave)

    def add(self,c):
        if self.running: raise ValueError('Stop the running batch before modifying its queue.')
        c=deepcopy(c)
        key=os.path.normcase(str(Path(c['video']).resolve()))
        for i,old in enumerate(self.items):
            if os.path.normcase(str(Path(old['video']).resolve())) == key:
                self.items[i]=c; break
        else: self.items.append(c)
        self.persist(); self.refresh()

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        for i,c in enumerate(self.items):
            sessions=[j for j in self.items if j['animal'].strip()==c['animal'].strip()]
            enc=[j for j in sessions if j['session']=='encoding']
            tests=[j for j in sessions if j['session']=='test']
            pair='Paired' if len(enc)==len(tests)==1 else 'Duplicate' if len(enc)>1 or len(tests)>1 else 'Calibration file' if c.get('calibration') else 'Unpaired'
            radius=enc[0]['object_radius'] if len(enc)==1 else c['object_radius']
            multiplier=enc[0]['multiplier'] if len(enc)==1 else c['multiplier']
            self.tree.insert('', 'end', iid=str(i),values=(c['animal'],c['session'],Path(c['video']).name,c['shape'],
                f'{radius:.3g}',f'{multiplier:g}',pair,c.get('queue_status','Ready' if c.get('ready') else 'Needs setup')))

    def import_folder(self):
        folder=filedialog.askdirectory(initialdir=self.app_reference()/'metadata',parent=self.window)
        if not folder: return
        errors=[]; count=0
        for path in sorted(Path(folder).rglob('*_metadata.json')):
            try: self.add(core.from_metadata(path)); count+=1
            except Exception as e: errors.append(f'{path.name}: {e}')
        self.state.set(f'Imported {count} recordings. Validate pairs before starting.')
        if errors: messagebox.showerror('Some metadata could not be imported','\n'.join(errors),parent=self.window)

    def app_reference(self):
        from analyzer import REFERENCE
        return REFERENCE

    def add_videos(self):
        paths=filedialog.askopenfilenames(parent=self.window,initialdir=self.app_reference()/'video',filetypes=[('Videos','*.mp4 *.avi *.mov *.mkv')])
        errors=[]
        for path in paths:
            if any(os.path.normcase(c['video']) == os.path.normcase(str(Path(path).resolve())) for c in self.items): continue
            try: self.add(core.from_video(path))
            except Exception as e: errors.append(str(e))
        if errors: messagebox.showerror('Could not add videos','\n'.join(errors),parent=self.window)
        self.state.set('Videos added. Verify inferred animal IDs and sessions; configure each recording before running.')

    def edit(self):
        if self.running: return
        selection=self.tree.selection()
        if not selection: return
        try:
            c=deepcopy(self.items[int(selection[0])])
            # Show the encoding-derived radius while editing the test.
            if c['session']=='test':
                enc=[j for j in self.items if j['animal']==c['animal'] and j['session']=='encoding']
                if len(enc)==1: c.update(object_radius=enc[0]['object_radius'],multiplier=enc[0]['multiplier'])
            self.app.load_configuration(c)
            self.window.withdraw(); self.app.root.lift()
        except Exception as e: messagebox.showerror('Cannot configure',str(e),parent=self.window)

    def remove(self):
        for i in sorted(map(int,self.tree.selection()),reverse=True): self.items.pop(i)
        self.persist(); self.refresh()

    def save_queue(self):
        path=filedialog.asksaveasfilename(parent=self.window,defaultextension='.json',filetypes=[('Batch queue','*.json')])
        if path: Path(path).write_text(json.dumps(self.items,indent=2),encoding='utf-8')

    def load_queue(self):
        path=filedialog.askopenfilename(parent=self.window,filetypes=[('Batch queue','*.json')])
        if not path: return
        try:
            items=json.loads(Path(path).read_text(encoding='utf-8'))
            if not isinstance(items,list) or any(not isinstance(c,dict) or not all(k in c for k in ('video','animal','session','shape','object_radius','multiplier')) for c in items):
                raise ValueError('Not a saved batch queue.')
            self.items=items; self.persist(); self.refresh()
        except Exception as e: messagebox.showerror('Invalid queue',str(e),parent=self.window)

    def validate(self):
        try:
            jobs=core.plan_batch(self.items,HERE/'results'/'preflight')
            pairs=len({c['animal'] for c in jobs})
            self.state.set(f'Validated {len(jobs)} recordings for {pairs} animals. Encoding runs first; its saved calibration is used by the matching test.')
            return jobs
        except Exception as e:
            self.state.set(str(e)); messagebox.showerror('Batch validation',str(e),parent=self.window)
            return None

    def start(self):
        if self.running or self.app.process: return
        output=HERE/'results'/('batch_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
        try: jobs=core.plan_batch(self.items,output)
        except Exception as e: messagebox.showerror('Batch validation',str(e),parent=self.window); return
        self.jobs=jobs; self.output=output; output.mkdir(parents=True)
        self.running=True; self.index=0; self.run_token+=1
        for b in self.buttons: b.configure(state='disabled')
        self.stop_button.configure(state='normal')
        for widget in self.app.controls+[self.app.start_button,self.app.save_queue_button]: widget.configure(state='disabled')
        core.write_reports(self.jobs,self.output)
        self.next_job()

    def next_job(self):
        if self.index >= len(self.jobs): self.finish(); return
        c=self.jobs[self.index]
        if c['session']=='test':
            enc=next((j for j in self.jobs if j['animal']==c['animal'] and j['session']=='encoding'),None)
            if enc and enc['status']!='complete':
                c['status']='blocked: encoding failed'; self.update_status(c); self.index+=1; self.next_job(); return
        folder=Path(c['output']); folder.mkdir(parents=True,exist_ok=True)
        path=folder/'settings.json'; path.write_text(json.dumps(c,indent=2),encoding='utf-8')
        c['status']='running'
        self.state.set(f'{self.index+1}/{len(self.jobs)} · {c["animal"]} · {c["session"]} · r={c["object_radius"]:g}px, zone={c["multiplier"]:g}×r')
        self.update_status(c)
        core.write_reports(self.jobs,self.output)
        token,index=self.run_token,self.index
        try:
            self.process=subprocess.Popen([sys.executable,'-u',str(HERE/'analyzer.py'),'--run',str(path)],
                stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',
                creationflags=subprocess.CREATE_NO_WINDOW,env=os.environ.copy())
        except OSError as e:
            (folder/'INCOMPLETE.txt').write_text(str(e),encoding='utf-8'); self.events.put(('done',(token,index,1))); return
        def reader(process):
            with (folder/'analysis.log').open('w',encoding='utf-8') as log:
                for line in process.stdout:
                    log.write(line); log.flush(); self.app.events.put(('log',line))
            self.events.put(('done',(token,index,process.wait())))
        threading.Thread(target=reader,args=(self.process,),daemon=True).start()

    def update_status(self,c):
        for item in self.items:
            if item['video']==c['video']: item['queue_status']=c['status']
        self.refresh()

    def poll(self):
        try:
            while True:
                _,(token,index,code)=self.events.get_nowait()
                if not self.running or token!=self.run_token or index!=self.index: continue
                self.process=None
                c=self.jobs[self.index]
                c['status']='complete' if code==0 and (Path(c['output'])/'COMPLETE.txt').exists() else 'failed'
                if c['status']=='failed': (Path(c['output'])/'INCOMPLETE.txt').write_text('Analysis failed. See analysis.log.',encoding='utf-8')
                self.update_status(c); self.index+=1
                core.write_reports(self.jobs,self.output)
                self.next_job()
        except queue.Empty: pass
        self.window.after(150,self.poll)

    def stop(self):
        if not self.running: return
        if self.process: self.process.terminate()
        for c in self.jobs:
            if c['status'] in ('pending','running'):
                if c['status']=='running': (Path(c['output'])/'INCOMPLETE.txt').write_text('Batch stopped.',encoding='utf-8')
                c['status']='cancelled'; self.update_status(c)
        self.finish(stopped=True)

    def finish(self,stopped=False):
        self.running=False
        core.write_reports(self.jobs,self.output)
        for b in self.buttons: b.configure(state='normal')
        self.stop_button.configure(state='disabled')
        for widget in self.app.controls+[self.app.start_button,self.app.save_queue_button]:
            widget.configure(state='readonly' if isinstance(widget,ttk.Combobox) else 'normal')
        complete=sum(c['status']=='complete' for c in self.jobs)
        self.state.set(f'{"Stopped" if stopped else "Finished"}: {complete}/{len(self.jobs)} recordings complete. See paired_animals.csv and batch_sessions.csv.')
        (self.output/('COMPLETE.txt' if complete==len(self.jobs) else 'INCOMPLETE.txt')).write_text(self.state.get(),encoding='utf-8')
        self.persist()

    def open_results(self):
        self.output.mkdir(parents=True,exist_ok=True); os.startfile(str(self.output))

    def hide(self):
        self.window.withdraw()
