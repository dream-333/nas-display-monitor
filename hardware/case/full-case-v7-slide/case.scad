// V7 slide rails + transverse tail lock. ABS-GF / X1C / 0.4mm.
// Dimensions from simplified official STEP. Prototype: actual fit unverified.
part="base";
$fn=48;
pcb_w=25.49; pcb_l=58.782; bx=5.8; by=3; pcb_z=9;
w=37.09;l=68.782;wall=1.8;seat=14.9;lid_t=2;
button_x=bx+3.9555;button_y=by+2.202;
module box(x,y,z,a,b,c){translate([x,y,z])cube([a,b,c]);}
module rounded(ww,ll,h,r=2){hull()for(x=[r,ww-r],y=[r,ll-r])translate([x,y,0])cylinder(r=r,h=h);}
module mirror_pair(){children();translate([w,0,0])mirror([1,0,0])children();}
// Continuous female guide, receiving an L flange without flexing any feature.
module rail_void(){
 box(1.8,-2,10.7,3.25,l+4,2.5);
 box(3.2,-2,13.2,1.85,l+4,2.0);
}
module lock_bore(){box(-2,65.4,11.1,w+4,2.6,2.6);}
module base(){intersection(){rounded(w,l,seat);difference(){union(){
 difference(){
  rounded(w,l,seat);
  box(wall,wall,wall,w-2*wall,l-2*wall,seat+1);
  box(w/2-8,-1,6.2,16,wall+2,9);
  // Front upper wall is closed by the lid apron in the assembled state.
  box(-1,-1,13.4,w+2,wall+1,3);
 }
 // Rigid rails attached continuously to side wall: no cantilever snap stems.
 mirror_pair()box(1.4,wall,10.7,3.6,l-wall,4.2);
 // Eight PCB edge supports; no glass supports.
 for(y=[3.5,21,45,56.782])mirror_pair(){
  box(1.4,y,wall,5.2,4,7.9-wall);
  box(4.65,y,7.9,1,4,5.0);
 }
 box(bx+2,61.932,wall,pcb_w-4,1.2,8.2);
 for(x=[bx,bx+pcb_w-1])box(x,1.85,wall,1,1,8.0);
 }
 mirror_pair()rail_void();
 mirror_pair()box(1.8,-1,13.2,3.25,2.8,2);
 lock_bore();
 for(y=[18:6:54])hull()for(x=[12,25])translate([x,y,-.1])cylinder(d=2.4,h=2.1);
}}}
module key_outline(){
 translate([button_x,button_y])circle(r=2.2);
 translate([button_x,button_y-2.2])square([17.9-button_x,4.4]);
}
module key_cut(){translate([0,0,-.1])linear_extrude(height=2.2)intersection(){offset(r=.5)key_outline();square([17.5,12]);}}
module button(){
 translate([0,0,1.4])linear_extrude(height=.6)key_outline();
 // Broad short head, only 1.0mm projection below lid underside.
 // Bottom z13.9: 0.4mm above modelled stock frame, no nominal preload.
 translate([button_x,button_y,0])cylinder(d=4.2,h=1.5);
 translate([button_x,button_y,-1])cylinder(d1=3.2,d2=4.2,h=1.05);
 hull(){box(16.5,button_y-2.2,1.4,.1,4.4,.6);box(17.9,button_y-2.2,0,.1,4.4,2);}
}
module flange(y,len){
 box(2,y,-4,2.8,len,2.1);
 box(3.4,y,-1.9,1.4,len,2.0);
}
module frame_stop(y){
 // Short rigid ledge over stock frame outer 0.25mm; does not grip glass.
 box(3.4,y,-1.25,2.65,3,1.35);
}
module lid(){difference(){union(){
 difference(){
  rounded(w,l,lid_t);
  hull(){box(8.045,13.4,-.1,21,46,.02);box(7.345,12.7,2.1,22.4,47.4,.02);}
  mirror_pair()key_cut();
 }
 mirror_pair()button();
 mirror_pair(){
  for(y=[5,24,43])flange(y,8);
  flange(61,7.4);
  frame_stop(21);frame_stop(49);
 }
 // Closes the rail entrances and the front upper wall when lid reaches home.
 difference(){
  intersection(){box(0,0,-1.5,w,1.8,1.6);translate([0,0,-1.5])rounded(w,l,1.6);}
  box(w/2-8,-1,-9,16,4,9.1);
 }
 intersection(){mirror_pair()box(1.95,0,-4.1,2.95,1.8,4.2);translate([0,0,-4.1])rounded(w,l,4.2);}
 }
 translate([0,0,-seat])lock_bore();
}}
// Transverse, flat-printed rigid lock. Retention taper is limited to last 3mm.
// Bare shaft 2.4 x 2.4 in 2.6 x 2.6 bore; root 2.65mm taper lightly wedges.
module lock_assembled(){
 box(-.8,65.3,11.2,1.0,2.8,2.6);
 hull(){box(.1,65.375,11.2,.1,2.65,2.55);box(3,65.5,11.2,.1,2.4,2.4);}
 box(3,65.5,11.2,w-3.2,2.4,2.4);
}
if(part=="base")base();
if(part=="lid")translate([0,l,lid_t])rotate([180,0,0])lid();
if(part=="lock")translate([.8,-65.3,-11.2])lock_assembled();
if(part=="assembly"){base();translate([0,0,seat])lid();lock_assembled();}
