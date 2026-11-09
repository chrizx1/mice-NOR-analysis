"""Persistent recording setup, exact animal pairing, and batch result aggregation."""
from pathlib import Path
from copy import deepcopy
import json
import math
import re
import cv2
import numpy as np

DEFAULTS = dict(angle=90., bout=.2, gap=.2, threshold=25., min_area=150., max_area=7000.,
                multiplier=4., object_radius=12., positive=2, minutes=10., experiment='Two-object',
                immobility_seconds=10.,immobility_tolerance=2.)


def default_minutes(session):
    return 10. if session == 'encoding' else 5.


def safe_name(text):
    return re.sub(r'[^A-Za-z0-9_.-]+', '_', text).strip('._') or 'animal'


def read_calibration(path, animal):
    p = Path(path)
    if not p.is_file(): raise ValueError(f'{animal}: encoding calibration not found: {path}')
    data = json.loads(p.read_text(encoding='utf-8'))
    if str(data.get('animal_id', '')).strip() != animal:
        raise ValueError(f'{animal}: calibration belongs to a different animal ({data.get("animal_id")}).')
    radius = float(data['common_object_radius_px'])
    if not math.isfinite(radius) or radius <= 0: raise ValueError(f'{animal}: invalid calibration radius.')
    return data


def video_info(path):
    cap = cv2.VideoCapture(str(path))
    ok, frame = cap.read()
    fps, count = cap.get(cv2.CAP_PROP_FPS), cap.get(cv2.CAP_PROP_FRAME_COUNT)
    cap.release()
    if not ok or not math.isfinite(fps) or fps <= 0 or count < 1:
        raise ValueError(f'Cannot read video or frame rate: {path}')
    return frame, fps, count


def from_video(path):
    path = Path(path).resolve()
    frame, fps, count = video_info(path)
    h,w = frame.shape[:2]
    match = re.search(r'(?i)[_ -](encoding|encode|testing|test)$', path.stem)
    session = 'encoding' if match and match.group(1).lower() in ('encoding','encode') else 'test' if match else 'encoding'
    animal = path.stem[:match.start()] if match else path.stem
    return dict(DEFAULTS, video=str(path), animal=animal, session=session, shape='Circular',
                cx=w/2, cy=h/2, size=min(w,h)*.4, minutes=default_minutes(session),
                objects=[None,None], calibration='', ready=False)


def from_metadata(path):
    path = Path(path).resolve()
    m = json.loads(path.read_text(encoding='utf-8'))
    video = Path(m['video_path'])
    if not video.is_file():
        sibling = path.parent / m.get('video_filename', video.name)
        if sibling.is_file(): video = sibling
    c = dict(DEFAULTS, animal=str(m['animal_id']).strip(), session=m['session_type'].lower(),
             video=str(video.resolve()), objects=[m['object_1_center'], m['object_2_center']],
             calibration=m.get('calibration_path',''), source_metadata=str(path), ready=True)
    c['minutes'] = float(m.get('analysis_duration_minutes', float(m.get('analysis_duration_seconds',default_minutes(c['session'])*60))/60))
    c['positive'] = int(m.get('preference_positive_object',2))
    c['object_radius'] = float(m['common_object_radius_px'])
    c['multiplier'] = float(m.get('investigation_radius_multiplier',4))
    if m.get('arena_shape') in ('circle','circular','Circular'):
        c.update(shape='Circular',cx=float(m['arena_center'][0]),cy=float(m['arena_center'][1]),size=float(m['arena_radius_px']))
    else:
        points = np.asarray(m['arena_points'],dtype=float)
        c.update(shape='Square',cx=float(points[:,0].mean()),cy=float(points[:,1].mean()),
                 size=float(max(np.ptp(points[:,0]),np.ptp(points[:,1]))), arena_points=points.tolist())
    radii = [m.get(f'encoding_selected_object_{i}_radius_px') for i in (1,2)]
    if all(r is not None for r in radii):
        c['encoding_radii'] = list(map(float,radii))
        c['object_radius'] = sum(c['encoding_radii'])/2
    return c


def geometry(c, width, height):
    from analyzer import arena_geometry
    if c.get('arena_points') and c['shape'] == 'Square':
        points = np.asarray(c['arena_points'],dtype=float)
        if points.shape != (4,2) or not np.isfinite(points).all(): raise ValueError('Invalid four-corner arena.')
        if (points < 0).any() or (points[:,0] >= width).any() or (points[:,1] >= height).any():
            raise ValueError('Arena corners must fit inside the video.')
        if not cv2.isContourConvex(points.astype(np.int32)): raise ValueError('Arena corners must form a convex polygon.')
        x,y,w,h = cv2.boundingRect(points.astype(np.int32))
        mask = np.zeros((h,w),np.uint8)
        cv2.fillPoly(mask,[(points-[x,y]).astype(np.int32)],255)
        return points,(x,y,w,h),mask
    return arena_geometry(c['shape'],c['cx'],c['cy'],c['size'],width,height)


def validate(c):
    if not c.get('ready',True): raise ValueError('Configure this recording and save it to the batch first.')
    if not c['animal'].strip(): raise ValueError('Animal ID is required.')
    if c['session'] not in ('encoding','test'): raise ValueError('Session must be encoding or test.')
    if c['shape'] not in ('Circular','Square'): raise ValueError('Choose a circular or square arena.')
    for k in ('minutes','cx','cy','size','object_radius','multiplier','angle','bout','gap','threshold','min_area','max_area'):
        if not math.isfinite(float(c[k])): raise ValueError(f'{k} must be finite.')
    if any(c[k] <= 0 for k in ('minutes','size','object_radius','min_area')): raise ValueError('Sizes and duration must be positive.')
    if c['multiplier'] <= 1 or not 0 <= c['angle'] <= 180: raise ValueError('Invalid zone multiplier or angle.')
    if min(c['bout'],c['gap']) < 0 or not 0 < c['threshold'] <= 255 or c['max_area'] <= c['min_area']:
        raise ValueError('Invalid bout, gap, threshold or mouse area limits.')
    if c['positive'] not in (1,2): raise ValueError('Positive object must be 1 or 2.')
    for key in ('immobility_seconds','immobility_tolerance'):
        value=float(c.get(key,DEFAULTS[key]))
        if not math.isfinite(value) or value < 0: raise ValueError('Immobility settings must be finite and nonnegative.')
    if c.get('encoding_radii'):
        if len(c['encoding_radii']) != 2 or any(not math.isfinite(r) or r <= 0 for r in c['encoding_radii']):
            raise ValueError('Both encoding radii must be positive.')
        if not math.isclose(sum(c['encoding_radii'])/2,c['object_radius']): raise ValueError('Common radius must equal the mean encoding radius.')
    frame,_,_ = video_info(c['video'])
    h,w = frame.shape[:2]
    _,(x,y,_,_),mask = geometry(c,w,h)
    if len(c['objects']) != 2: raise ValueError('Two object centers are required.')
    for point in c['objects']:
        if point is None or len(point) != 2 or not all(math.isfinite(v) for v in point): raise ValueError('Place both object centers.')
        px,py = round(point[0])-x,round(point[1])-y
        if not (0 <= py < mask.shape[0] and 0 <= px < mask.shape[1] and mask[py,px]):
            raise ValueError('Both object centers must be inside the arena.')


def plan_batch(recordings, output):
    """One encoding and one test per exact animal ID; never pair by list position."""
    groups = {}
    for source in recordings:
        c = deepcopy(source)
        for key in ('immobility_seconds','immobility_tolerance'): c.setdefault(key,DEFAULTS[key])
        c['animal'] = c['animal'].strip()
        sessions = groups.setdefault(c['animal'],{})
        if c['session'] in sessions: raise ValueError(f'{c["animal"]}: duplicate {c["session"]} sessions. Use distinct IDs for different visits.')
        sessions[c['session']] = c
    if not groups: raise ValueError('Add recordings to the batch first.')
    jobs = []
    for index,(animal,sessions) in enumerate(sorted(groups.items()),1):
        folder = Path(output)/f'{index:03d}_{safe_name(animal)}'
        enc = sessions.get('encoding')
        if enc:
            enc['object_radius'] = sum(enc.get('encoding_radii',[enc['object_radius']]*2))/2
            calibration = folder/'encoding'/f'{safe_name(animal)}_object_calibration.json'
            shared = dict(object_radius=enc['object_radius'],multiplier=enc['multiplier'])
        else:
            test = sessions.get('test')
            if not test or not test.get('calibration'): raise ValueError(f'{animal}: missing encoding session or existing matching calibration.')
            data = read_calibration(test['calibration'],animal)
            calibration = Path(test['calibration']).resolve()
            shared = dict(object_radius=float(data['common_object_radius_px']),multiplier=float(data.get('investigation_radius_multiplier',test['multiplier'])))
        for session in ('encoding','test'):
            if session not in sessions: continue
            c = sessions[session]
            c.update(shared,output=str((folder/session).resolve()),calibration=str(calibration.resolve()),
                     calibration_source='queued encoding' if enc else 'existing encoding calibration', status='pending')
            if session == 'test': c.pop('encoding_radii',None)
            try: validate(c)
            except Exception as e: raise ValueError(f'{animal} / {session}: {e}') from e
            jobs.append(c)
    return jobs


def write_reports(jobs, output):
    import pandas as pd
    root = Path(output)
    (root/'batch_manifest.json').write_text(json.dumps(jobs,indent=2),encoding='utf-8')
    rows = []
    for c in jobs:
        if c['status'] != 'complete': continue
        files = list(Path(c['output']).glob('*summary.csv'))
        if not files: continue
        row = pd.read_csv(files[0]).iloc[0].to_dict()
        row.update(animal_id=c['animal'],session_type=c['session'],result_folder=c['output'])
        rows.append(row)
    pd.DataFrame(rows).to_csv(root/'batch_sessions.csv',index=False)
    paired = []
    for animal in sorted({c['animal'] for c in jobs}):
        pair = dict(animal_id=animal)
        for c in (j for j in jobs if j['animal'] == animal):
            prefix = c['session']+'_'
            pair[prefix+'status'] = c['status']
            pair[prefix+'result_folder'] = c['output']
            pair['common_object_radius_px'] = c['object_radius']
            pair['investigation_radius_multiplier'] = c['multiplier']
            for row in rows:
                if row['animal_id'] == animal and row['session_type'] == c['session']:
                    for key in ('object_1_investigation_seconds','object_2_investigation_seconds',
                                'total_investigation_seconds','preference_index','mouse_detection_rate_percent',
                                'orientation_confidence_rate_percent'):
                        pair[prefix+key] = row.get(key)
                    for key in ('immobility_interval_count','immobility_flagged_seconds','investigation_immobility_flagged_seconds',
                                'immobility_action','immobility_excluded_investigation_seconds',
                                'post_immobility_short_bout_excluded_seconds',
                                'object_1_immobility_excluded_seconds','object_2_immobility_excluded_seconds',
                                'object_1_investigation_before_immobility_seconds','object_2_investigation_before_immobility_seconds'):
                        pair[prefix+key] = row.get(key)
        paired.append(pair)
    pd.DataFrame(paired).to_csv(root/'paired_animals.csv',index=False)
