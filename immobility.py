"""Review flags based on sustained head/body position stability; never changes scoring."""
import math
import numpy as np
import pandas as pd

INTERVAL_COLUMNS = ['interval_id','start_frame','end_frame','start_seconds','end_seconds',
                    'duration_seconds','investigation_flagged_seconds','objects']


def flag_frames(table, fps, duration_seconds=10., tolerance_px=2.):
    if not math.isfinite(fps) or fps <= 0: raise ValueError('FPS must be positive.')
    if not math.isfinite(duration_seconds) or duration_seconds < 0: raise ValueError('Immobility duration must be nonnegative.')
    if not math.isfinite(tolerance_px) or tolerance_px < 0: raise ValueError('Motion tolerance must be nonnegative.')
    table = table.copy()
    count = len(table)
    flags = np.zeros(count,dtype=bool)
    elapsed = np.zeros(count,dtype=float)
    ids = np.zeros(count,dtype=int)
    intervals = []
    if duration_seconds > 0 and count:
        points = table[['body_x','body_y','head_x','head_y']].to_numpy(dtype=float)
        valid = table['mouse_detected'].to_numpy(dtype=bool) & np.isfinite(points).all(axis=1)
        start = None

        def finish(end):
            if start is None: return
            duration = (end-start)/fps
            # Strictly greater than threshold. Flag the whole interval for retrospective review.
            if duration <= duration_seconds: return
            interval_id = len(intervals)+1
            flags[start:end] = True
            ids[start:end] = interval_id
            states = table['cleaned_state'].iloc[start:end].to_numpy()
            intervals.append(dict(interval_id=interval_id,
                start_frame=int(table['frame'].iloc[start]),end_frame=int(table['frame'].iloc[end-1]),
                start_seconds=float(table['time_seconds'].iloc[start]),
                end_seconds=float(table['time_seconds'].iloc[end-1])+1/fps,
                duration_seconds=duration,investigation_flagged_seconds=float(np.isin(states,[1,2]).sum()/fps),
                objects=','.join(str(i) for i in (1,2) if (states==i).any())))

        for i in range(count):
            if not valid[i]:
                finish(i); start=None; continue
            if start is None:
                start=i
            else:
                body_motion=np.linalg.norm(points[i,:2]-points[start,:2])
                head_motion=np.linalg.norm(points[i,2:]-points[start,2:])
                if max(body_motion,head_motion) > tolerance_px:
                    finish(i); start=i
            elapsed[i]=(i-start+1)/fps
        finish(count)
    table['stationary_duration_seconds']=elapsed
    table['immobility_flag']=flags
    table['immobility_interval_id']=ids
    table['investigation_immobility_flag']=flags & table['cleaned_state'].isin([1,2]).to_numpy()
    return table,pd.DataFrame(intervals,columns=INTERVAL_COLUMNS)


def flag_bouts(bouts, frames, fps):
    bouts=bouts.copy()
    # extract_bouts uses frame indices beginning at 1 and inclusive end frames.
    bouts['immobility_flag']=False
    bouts['immobility_flagged_seconds']=0.
    for index,row in bouts.iterrows():
        overlap=frames['frame'].between(int(row['start_frame']),int(row['end_frame']))
        flagged=frames.loc[overlap,'investigation_immobility_flag']
        bouts.loc[index,'immobility_flag']=bool(flagged.any())
        bouts.loc[index,'immobility_flagged_seconds']=float(flagged.sum()/fps)
    return bouts
