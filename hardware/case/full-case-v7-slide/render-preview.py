from pathlib import Path
import os
import vtk
import numpy as np
p=Path(__file__).resolve().parent
out=Path(os.environ.get('NAS_DISPLAY_MECHANICAL_OUTPUT', p.parents[2]/'.build/mechanical/v7'))
data=np.load(out/'preview-data.npz')
window=vtk.vtkRenderWindow();window.SetOffScreenRendering(1);window.SetSize(1600,800)
for i,(pos,up,title) in enumerate([((32,18,160),(0,1,0),'V7 - SLIDE / NO SNAP ARMS'),((-75,-100,100),(0,0,1),'V7 - RAILS / TAIL LOCK')]):
 r=vtk.vtkRenderer();r.SetViewport(i*.5,0,(i+1)*.5,1);r.SetBackground(.92,.94,.96);window.AddRenderer(r)
 for name,col in [('base',(.22,.24,.27)),('lid',(.28,.30,.33)),('lock',(.65,.44,.2))]:
  if i==1 and name=='lid':continue
  points=vtk.vtkPoints();cells=vtk.vtkCellArray()
  for tri in data[name][:,:,[1,0,2]]:
   cell=vtk.vtkTriangle()
   for j,v in enumerate(tri):cell.GetPointIds().SetId(j,points.InsertNextPoint(*v))
   cells.InsertNextCell(cell)
  poly=vtk.vtkPolyData();poly.SetPoints(points);poly.SetPolys(cells);mapper=vtk.vtkPolyDataMapper();mapper.SetInputData(poly);actor=vtk.vtkActor();actor.SetMapper(mapper);actor.GetProperty().SetColor(*col);r.AddActor(actor)
 plane=vtk.vtkPlaneSource();plane.SetOrigin(13.4,7,14.5);plane.SetPoint1(59.4,7,14.5);plane.SetPoint2(13.4,30,14.5);mapper=vtk.vtkPolyDataMapper();mapper.SetInputConnection(plane.GetOutputPort());actor=vtk.vtkActor();actor.SetMapper(mapper);actor.GetProperty().SetColor(.04,.07,.09);
 if i==0:r.AddActor(actor)
 camera=r.GetActiveCamera();camera.SetPosition(*pos);camera.SetFocalPoint(34,18,9);camera.SetViewUp(*up);camera.ParallelProjectionOn();camera.SetParallelScale(43)
 text=vtk.vtkTextActor();text.SetInput(title);text.SetPosition(30,730);text.GetTextProperty().SetFontSize(25);text.GetTextProperty().SetColor(.15,.2,.25);r.AddActor2D(text)
window.Render();capture=vtk.vtkWindowToImageFilter();capture.SetInput(window);capture.Update();writer=vtk.vtkPNGWriter();writer.SetFileName(str(out/'preview.png'));writer.SetInputConnection(capture.GetOutputPort());writer.Write()
