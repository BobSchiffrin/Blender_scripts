"""
Protein-bead generator for Blender (v3.0 – Fixed Boolean Operations)
=====================================================================
Creates complementary SOLID hemispheres ("beads") whose halves bind only
when their lock-and-key features align. Designed for 3D printing.

v3.0 improvements:
* Fixed BOTH Boolean operations (UNION and DIFFERENCE) working reliably
* Properly positioned pegs (UNION) and sockets (DIFFERENCE) with correct overlap
* Optimized Z-positioning formulas for both operations
* Combined best practices from previous versions
* Uses the FAST solver which is more tolerant than EXACT for this workflow
* Applies scale before boolean operations to prevent errors
"""

import bpy
import random
import math
from mathutils import Vector
import os
import tempfile
import datetime
import traceback
import bmesh
from mathutils import noise

# ============================ USER SETTINGS ============================ #
SPHERE_DIAMETER = 60.0      # mm outer diameter of the complete bead
WALL_THICKNESS = 3.0        # mm - Used for socket depth calculation
PEG_LENGTH = 20.0           # mm total length of every peg
CLEARANCE = 0.50            # mm radial clearance per side for sockets
OVERLAP = 11# 0.25              # mm - How much peg/cutter overlaps with hemisphere
BEAD_COUNT = 4              # how many bead pairs to make
KEY_SHAPES = ["cylinder", "triangle", "square",'hexagon']
KEYS_PER_BEAD = len(KEY_SHAPES)  # one of each shape per bead
SEED = 2057               # RNG seed for reproducibility
EXPORT_SUBFOLDER = "beads/" # Folder name for outputs relative to the .blend file

OUTER_R = SPHERE_DIAMETER / 2.0
INNER_R = OUTER_R - WALL_THICKNESS  # Usable depth for sockets within the solid base
MAX_KEY_RADIUS = 6.0        # mm - Radius for the pegs

# === USER NOISE PARAMETERS ===
ADD_SURFACE_NOISE = True
segments = 64         # horizontal subdivisions
ring_count = 32            # vertical subdivisions
noise_strength = 0.3 * OUTER_R # how far vertices move (user-defined "amount")
noise_scale = 2.0  / OUTER_R   # frequency of the noise pattern
# =======================

# ============================ LOGGING SETUP ============================ #
log_file = None

def log_message(message):
    """Writes a timestamped message to the log file and also prints to console as a fallback."""
    global log_file
    timestamp = datetime.datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    full_message = f"{timestamp} {message}"
    if log_file and not log_file.closed:
        try:
            log_file.write(full_message + "\n")
            log_file.flush()  # Ensure message is written immediately
        except Exception as e:
            # Fallback to printing if file writing fails
            print(f"Error writing to log file: {e}")
            print(full_message)
    else:
        # Fallback to printing if log file is not set up or is closed
        print(full_message)

# ========================= GEOMETRY HELPERS =========================== #

def wipe_scene():
    """Clears all objects from the scene."""
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete()



# 
# protein‑surface noisey sphere --------------------------------------------------

def make_noisy_sphere():
    # Create a UV sphere
    bpy.ops.mesh.primitive_uv_sphere_add(
        segments=segments,
        ring_count=ring_count,
        radius=OUTER_R,
        enter_editmode=False
    )

    obj = bpy.context.active_object
    mesh = obj.data

    # Switch to BMesh for safe mesh editing
    bm = bmesh.new()
    bm.from_mesh(mesh)

    # Displace each vertex along its normal by a Perlin noise value
    for v in bm.verts:
        # Sample 3D Perlin noise at the vertex coordinate (scaled)
        n = noise.noise(v.co * noise_scale)
        # Move vertex along its own normal
        v.co += v.co.normalized() * n * noise_strength

    # Write the BMesh back to the mesh and free
    bm.to_mesh(mesh)
    bm.free()

    # Smooth shading
    bpy.ops.object.shade_smooth()

    return obj


def make_solid_hemisphere(is_top: bool):
    """Return a solid hemisphere mesh object."""
    # Create a sphere
     # Add noise BEFORE bisecting if enabled
    if ADD_SURFACE_NOISE:
        log_message("  Adding surface noise...")
        hemisphere = make_noisy_sphere()
        log_message("  Surface noise applied.") 

    else:    
        # Create a sphere
        bpy.ops.mesh.primitive_uv_sphere_add(radius=OUTER_R, segments=64, ring_count=32)
        hemisphere = bpy.context.active_object
        hemisphere.name = "Hemisphere_Top" if is_top else "Hemisphere_Bot"


    # Cut the sphere into a solid hemisphere at Z=0
    bpy.ops.object.mode_set(mode='EDIT')
    plane_no = (0, 0, 1) if is_top else (0, 0, -1)
    bpy.ops.mesh.select_all(action='SELECT')
    # Bisect the mesh at the Z=0 plane, keeping the relevant half and filling the cut face
    bpy.ops.mesh.bisect(plane_co=(0, 0, 0), plane_no=plane_no,
                        clear_inner=True, clear_outer=False, use_fill=True)
    bpy.ops.object.mode_set(mode='OBJECT')
    
    # Apply any scales to ensure boolean operations work correctly
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return hemisphere

# ---------- primitive factories ---------- #
# These functions create basic shapes centered at their origin (0,0,0)

def cylinder(radius, height):
    bpy.ops.mesh.primitive_cylinder_add(radius=radius, depth=height)
    return bpy.context.active_object


def triangular_prism(radius, height):
    bpy.ops.mesh.primitive_cone_add(radius1=radius, radius2=radius, depth=height, vertices=3)
    return bpy.context.active_object


def square_prism(radius, height):
    bpy.ops.mesh.primitive_cube_add(size=radius * 2)
    obj = bpy.context.active_object
    obj.scale.z = height / (radius * 2)
    bpy.ops.object.transform_apply(scale=True)
    return obj


def hexagon(radius, height):
    bpy.ops.mesh.primitive_cylinder_add(radius=radius, depth=height, vertices=6)
    return bpy.context.active_object

SHAPE_FACTORIES = {
    "cylinder": cylinder,
    "triangle": triangular_prism,
    "square": square_prism,
    "hexagon": hexagon
}

# ---------- key placement ---------- #

def random_key_positions(n):
    """Generate n random (x, y) coordinates within the base area, avoiding overlaps."""
    coords = []
    attempts = 0
    usable_radius_for_keys = INNER_R - MAX_KEY_RADIUS  # Ensure key fits within the flat base
    while len(coords) < n and attempts < 1000:
        r = random.uniform(0, usable_radius_for_keys)
        ang = random.uniform(0, 2 * math.pi)
        x, y = r * math.cos(ang), r * math.sin(ang)
        if all(math.hypot(x - cx, y - cy) > MAX_KEY_RADIUS * 2 for cx, cy in coords):
            coords.append((x, y))
        attempts += 1
    return coords

# ---------- boolean helper ---------- #

def boolean(target, tool, op_type):
    """Apply a boolean modifier (DIFFERENCE, UNION, INTERSECT) to target using tool."""
    mod = target.modifiers.new(name="Bool", type='BOOLEAN')
    mod.object = tool
    mod.operation = op_type
    mod.solver = 'FAST'  # FAST is more tolerant than EXACT for this workflow
    bpy.context.view_layer.objects.active = target
    
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    
    log_message(f"    Applying boolean '{op_type}' on {target.name} with {tool.name}")
    try:
        bpy.ops.object.modifier_apply(modifier=mod.name)
        log_message(f"    Boolean applied successfully.")
    except RuntimeError as e:
        log_message(f"    Error applying boolean modifier: {e}")


def add_keys(hemisphere, key_specs, is_top):
    """Add pegs (if is_top, UNION) or sockets (if not is_top, DIFFERENCE) to the solid hemisphere."""
    # Make sure we're in object mode
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    
    for i, (shape, (x, y)) in enumerate(key_specs, 1):
        factory = SHAPE_FACTORIES[shape]
        log_message(f"  Key {i}: {shape} @ ({x:.1f}, {y:.1f})")
        
        if is_top:
            # Create pegs for the top hemisphere (UNION operation)
            radius = MAX_KEY_RADIUS
            height = PEG_LENGTH
            key = factory(radius, height)
            key.name = f"Peg_{shape}_{i}"
            
            # Position for UNION: Peg should overlap with the hemisphere base
            # Place it so a portion of its top extends ABOVE z=0 by OVERLAP amount
            key_z = -(height/2 - OVERLAP)
            key.location = (x, y, key_z)
            
            boolean(hemisphere, key, 'UNION')
        else:
            # Create sockets for the bottom hemisphere (DIFFERENCE operation)
            radius = MAX_KEY_RADIUS + CLEARANCE
            height = PEG_LENGTH + CLEARANCE
            key = factory(radius, height)
            key.name = f"SocketCutter_{shape}_{i}"
            
            # Position for DIFFERENCE: Cutter should overlap with the hemisphere base
            # Place it so a portion of its top extends BELOW z=0 by OVERLAP amount
            key_z = (height/2 - OVERLAP)
            key.location = (x, y, key_z)
            
            boolean(hemisphere, key, 'DIFFERENCE')
        
        # Clean up key objects after boolean operations
        bpy.data.objects.remove(key, do_unlink=True)

    # Clean up the mesh after booleans
    log_message("  Performing mesh cleanup...")
    bpy.context.view_layer.objects.active = hemisphere
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    
    try:
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.remove_doubles()
        bpy.ops.mesh.normals_make_consistent(inside=False)
        bpy.ops.object.mode_set(mode='OBJECT')
        log_message("  Mesh cleanup successful.")
    except RuntimeError as e:
        log_message(f"  Error during mesh cleanup: {e}")


# ---------- export ---------- #

def export_stl(obj, folder, name):
    """Export an object as an STL file."""
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    path = os.path.join(folder, name)
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    log_message(f"Exporting {obj.name} to {path}")
    try:
        bpy.ops.export_mesh.stl(filepath=path, use_selection=True)
        log_message("Exported successfully.")
    except RuntimeError as e:
        log_message(f"Error exporting {obj.name}: {e}")


# ---------- Output Directory Helper ---------- #

def get_output_dir():
    """Determine the base directory for logs and exports."""
    blend_file_path = bpy.data.filepath
    if blend_file_path:
        # Use a subfolder within the .blend file's directory
        blend_dir = os.path.dirname(blend_file_path)
        output_dir = os.path.join(blend_dir, EXPORT_SUBFOLDER)
        try:
            os.makedirs(output_dir, exist_ok=True)
            return output_dir
        except OSError as e:
            print(f"Error creating output directory {output_dir}: {e}. Falling back to temp.")
            pass  # Fallback to temp if directory creation fails

    # Fallback to a temporary directory if the .blend file is not saved or dir creation failed
    temp_dir = os.path.join(tempfile.gettempdir(), "beads_output")
    try:
        os.makedirs(temp_dir, exist_ok=True)
        print(f"⚠️  .blend file not saved or directory creation failed. Using temporary directory: {temp_dir}")
        return temp_dir
    except OSError as e:
        print(f"FATAL ERROR: Could not create temporary directory {temp_dir}: {e}")
        return None  # Indicate a fatal error


# ============================== MAIN =============================== #

def generate_beads():
    """Generate the bead pairs with lock-and-key features."""
    global log_file

    # Determine the base directory for logs and exports
    base_output_dir = get_output_dir()

    if not base_output_dir:
        print("Script cannot proceed: Unable to determine or create output directory.")
        return  # Exit if output directory could not be determined

    # Determine log file path and open it
    log_filename = f"beads_log_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    log_filepath = os.path.join(base_output_dir, log_filename)

    try:
        log_file = open(log_filepath, "w")
        log_message("--- Protein-bead generator script started ---")
        log_message(f"Log file created at: {log_filepath}")
    except Exception as e:
        print(f"CRITICAL ERROR: Could not open log file {log_filepath}: {e}. Messages will print to console.")
        log_file = None

    # Script execution logic
    try:
        random.seed(SEED)
        if bpy.context.object and bpy.context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        wipe_scene()

        for idx in range(1, BEAD_COUNT + 1):
            specs = list(zip(KEY_SHAPES, random_key_positions(KEYS_PER_BEAD)))

            log_message(f"\n--- Generating Bead {idx} - Top Half (with pegs) ---")
            top = make_solid_hemisphere(True)
            add_keys(top, specs, True)

            log_message(f"\n--- Generating Bead {idx} - Bottom Half (with sockets) ---")
            bottom = make_solid_hemisphere(False)
            add_keys(bottom, specs, False)

            log_message(f"\n--- Exporting Bead {idx} Halves ---")
            export_stl(top, base_output_dir, f"bead_{idx}_top.stl")
            export_stl(bottom, base_output_dir, f"bead_{idx}_bottom.stl")

            log_message(f"\nBead {idx} layout:")
            for s, (x, y) in specs:
                log_message(f"  {s:8s}  (x={x:.1f}, y={y:.1f}) mm")
            log_message("-" * 40)

        log_message("\n--- Protein-bead generator script finished ---")

    except Exception as e:
        # Catch any unexpected errors during script execution and log them
        log_message(f"\n--- Script encountered a critical error: {e} ---")
        log_message("Traceback:")
        log_message(traceback.format_exc())

    finally:
        # Ensure the log file is closed
        if log_file and not log_file.closed:
            log_file.close()
            print(f"Script finished. Debugging log saved to: {log_filepath}")
        elif not log_file:
            print("Script finished. Debugging messages were printed to console (log file creation failed).")
        else:
            print("Script finished. Log file was already closed.")


# Ensure we are in OBJECT mode before starting the script execution
if bpy.context.object and bpy.context.object.mode != 'OBJECT':
    bpy.ops.object.mode_set(mode='OBJECT')

# Run the main function when the script is executed
if __name__ == "__main__":
    generate_beads()
