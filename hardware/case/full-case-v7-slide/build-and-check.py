from pathlib import Path
import os,subprocess,json,trimesh,numpy as np
p=Path(__file__).resolve().parent
out=Path(os.environ.get("NAS_DISPLAY_MECHANICAL_OUTPUT", p.parents[2]/".build/mechanical/v7"))
out.mkdir(parents=True,exist_ok=True)
for part,name in [('base','01-base'),('lid','02-lid'),('lock','03-tail-lock'),('assembly','REFERENCE-assembled-do-not-print')]:
 r=subprocess.run(['openscad','-o',str(out/(name+'.stl')),'-D',f'part="{part}"',str(p/'case.scad')],capture_output=True,text=True)
 (out/(name+'.build.log')).write_text(r.stderr);r.check_returncode()
base=trimesh.load_mesh(out/'01-base.stl');lid=trimesh.load_mesh(out/'02-lid.stl');lock=trimesh.load_mesh(out/'03-tail-lock.stl');rows={}
for n,m in [('base',base),('lid',lid),('lock',lock)]:
 assert m.is_watertight and m.is_volume and len(m.split())==1,n
 rows[n]={'bounds_mm':m.bounds.tolist(),'volume_mm3':float(m.volume),'watertight':True,'solids':1}
lid.apply_transform(trimesh.transformations.rotation_matrix(np.pi,[1,0,0]));lid.apply_translation([0,68.782,16.9])
lock.apply_translation([-.8,65.3,11.2])
def volume(a,b):
 h=trimesh.boolean.intersection([a,b],engine='manifold')
 return max(0,float(h.volume)) if len(h.faces) else 0
refs={};boards=[]
for i in range(6):
 m=trimesh.load_mesh(p.parent/f'official/step-solid-{i}.stl');m.apply_translation([5.8,3,9]);refs[f'board{i}']=m.triangles
 if i==1:
  keep=trimesh.creation.box(extents=[100,100,100]);keep.apply_translation([18,53,10]);m=trimesh.boolean.intersection([m,keep],engine='manifold')
 boards.append(m)
 for name,shell in [('base',base),('lid',lid),('lock',lock)]:
  v=volume(m,shell);assert v<.001,(i,name,v)
v=volume(base,lid);assert v<.001,('base/lid',v)
rows['lock_root_intentional_wedge_overlap_mm3']=volume(lock,base)+volume(lock,lid)
# Discrete assembly-path check: lid enters from USB end, lock absent.
for offset in np.arange(-70,0.01,1):
 moved=lid.copy();moved.apply_translation([0,offset,0])
 for i,m in enumerate([base]+boards):
  if (moved.bounds[1]<m.bounds[0]).any() or (m.bounds[1]<moved.bounds[0]).any():continue
  v=volume(moved,m);assert v<.001,('insertion',float(offset),i,v)
rows['insertion_path']={'axis':'Y, from USB end','from_mm':-70,'to_mm':0,'step_mm':1,'poses':71,'result':'no positive-volume rigid intersections at sampled poses'}
raised=lid.copy();raised.apply_translation([0,0,.4]);assert volume(raised,base)>.001
shifted=lid.copy();shifted.apply_translation([0,-1,0]);assert volume(shifted,lock)>volume(lid,lock)+.05
frame=boards[5].copy();frame.apply_translation([0,0,.3]);assert volume(frame,lid)>.001
rows['restraint_checks_mm3']={'lid_up_0.4':volume(raised,base),'lid_back_1_locked':volume(shifted,lock),'frame_up_0.3':volume(frame,lid)}
rows['clearances_mm']={'lid_to_glass':.4,'button_to_modelled_frame':.4,'rigid_frame_stop':.15,'rail_vertical':.2,'rail_wall_side':.2}
rows['limits']='Simplified STEP static and 1mm sampled insertion checks, not continuous collision detection or structural simulation. Display flex outside PCB boundary excluded; actual board, flex, switches, fit and strength unverified.'
(out/'checks.json').write_text(json.dumps(rows,indent=2)+'\n')
np.savez(out/'preview-data.npz',base=base.triangles,lid=lid.triangles,lock=lock.triangles,**refs)
print('PASS static meshes and reference interference checks')
