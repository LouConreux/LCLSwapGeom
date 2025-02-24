import os
import re
import sys
import numpy as np
from math import atan2, degrees
import pyFAI
from pyFAI.detectors import Detector
from PSCalib.UtilsConvert import header_crystfel, panel_constants_to_crystfel
from PSCalib.GeometryAccess import GeometryAccess
from PSCalib.SegGeometryStore import sgs
import PSCalib.GlobalUtils as gu

pyFAI.use_opencl = False

DETTYPE_TO_PARS = {
    'epix10k2m': ('EPIX10KA:V2','p0a0,p1a0,p2a0,p3a0,p4a0,p5a0,p6a0,p7a0,'\
                            'p8a0,p9a0,p10a0,p11a0,p12a0,p13a0,p14a0,p15a0'),
    'epix10kaquad': ('EPIX10KA:V2','p0a0,p1a0,p2a0,p3a0'),
    'jungfrau1m': ('JUNGFRAU:V2','p0a0,p1a0'),
    'jungfrau4m': ('JUNGFRAU:V2','p0a0,p1a0,p2a0,p3a0,p4a0,p5a0,p6a0,p7a0'),
    'cspad'   : ('SENS2X1:V1', 'p0a0,p0a2,p0a4,p0a6,p0a8,p0a10,p0a12,p0a14,'\
                            'p1a0,p1a2,p1a4,p1a6,p1a8,p1a10,p1a12,p1a14,'\
                            'p2a0,p2a2,p2a4,p2a6,p2a8,p2a10,p2a12,p2a14,'\
                            'p3a0,p3a2,p3a4,p3a6,p3a8,p3a10,p3a12,p3a14'),\
    'cspadv2' : ('SENS2X1:V1', 'p0a0,p1a0,p2a0,p3a0,p4a0,p5a0,p6a0,p7a0,'\
                            'p8a0,p9a0,p10a0,p11a0,p12a0,p13a0,p14a0,p15a0,'\
                            'p16a0,p17a0,p18a0,p19a0,p20a0,p21a0,p22a0,p23a0,'\
                            'p24a0,p25a0,p26a0,p27a0,p28a0,p29a0,p30a0,p31a0'),\
    'pnccd'   : ('MTRX:V2:512:512:75:75', 'p0a0,p1a0,p2a0,p3a0'),\
}

def get_beam_center(params):
    """
    From pyFAI calibration parameters, return the beam center in meters

    Parameters
    ----------
    params : list
        Detector parameters found by PyFAI calibration
    """
    dist = params[0]
    poni1 = params[1]
    poni2 = params[2]
    rot1 = params[3]
    rot2 = params[4]
    distance = dist / (np.cos(rot1) * np.cos(rot2))
    cx = poni1 + dist * np.tan(rot2) / np.cos(rot1)
    cy = poni2 - dist * np.tan(rot1)
    return distance, cx, cy

def get_detector(det_type, pixel_size=None, shape=None):
    """
    Instantiate a PyFAI Detector object based on the detector type

    Parameters
    ----------
    det_type : str
        Detector type
    """
    if det_type.lower() == "epix10k2m":
        return ePix10k2M(pixel_size=pixel_size, shape=shape)
    elif "epix10kaquad" in det_type.lower():
        return ePix10kaQuad(pixel_size=pixel_size, shape=shape)
    elif det_type.lower() == "jungfrau1m":
        return Jungfrau1M(pixel_size=pixel_size, shape=shape)
    elif det_type.lower() == "jungfrau4m":
        return Jungfrau4M(pixel_size=pixel_size, shape=shape)
    elif det_type.lower() == "rayonix":
        return Rayonix(pixel_size=pixel_size, shape=shape)
    else:
        raise ValueError("Detector type not recognized")

def pick_template(exp, det_type, src, pixel_size=None, shape=None):
    """
    Pick the appropriate psana format template based on the detector type
    Parameters pixel size, and shape are required for the Rayonix detector
    Populate the experiment calibration directory with the template

    Parameters
    ----------
    exp : str
        Experiment name
    det_type : str
        Detector type
    src : str
        Source name of end station
    pixel_size : float
        Pixel size in µm
    shape : tuple
        Detector shape
    """
    current_dir = os.path.dirname(__file__)
    template_file = os.path.join(current_dir, "templates", det_type, "0-end.data")
    if not os.path.exists(template_file):
        raise FileNotFoundError(f"Template not found for detector {det_type}.")
    
    with open(template_file, "r") as file:
        content = file.readlines()

    if det_type.lower() == "rayonix":
        for i, line in enumerate(content):
            if "MTRX:V2" in line and shape is not None and pixel_size is not None:
                updated_line = re.sub(
                    r"MTRX:V2:\d+:\d+:\d+:\d+",
                    f"MTRX:V2:{shape[0]}:{shape[1]}:{pixel_size}:{pixel_size}",
                    line
                )
                content[i] = updated_line
                break

    cdir = f"/sdf/data/lcls/ds/{exp[:3]}/{exp}/calib"
    group = gu.dic_det_tname_lower_to_calib_group.get(det_type.lower())
    type = "geometry"
    in_file = os.path.join(cdir, group, src, type, "0-end.data")
    os.makedirs(os.path.dirname(in_file), exist_ok=True)
    file = open(in_file, "w")
    file.writelines(content)
    file.close()
    return in_file

class ePix10k2M(Detector):
    """
    PyFAI Detector instance for the ePix10k2M
    """

    def __init__(
        self,
        pixel_size=None,
        shape=None,
        n_asics=4,
        asics_shape = (2, 2), # (rows, cols) = (ss, fs)
        **kwargs,
    ):
        if pixel_size is None:
            pixel_size = 0.0001
        if shape is None:
            shape = (16, 352, 384)
        self.raw_shape = shape
        self.n_modules = shape[0]
        self.n_asics = n_asics
        self.asics_shape = asics_shape
        self.ss_size = shape[1] // asics_shape[0]
        self.fs_size = shape[2] // asics_shape[1]
        self.pixel_size = pixel_size
        super().__init__(pixel1=pixel_size, pixel2=pixel_size, max_shape=(self.n_modules * asics_shape[0] * self.ss_size, asics_shape[1] * self.fs_size), **kwargs)

class ePix10kaQuad(Detector):
    """
    PyFAI Detector instance for the ePix100
    """

    def __init__(
        self,
        pixel_size=None,
        shape=None,
        n_asics=4,
        asics_shape = (2, 2), # (rows, cols) = (ss, fs)
        **kwargs,
    ):
        if pixel_size is None:
            pixel_size = 0.0001
        if shape is None:
            shape = (4, 352, 384)
        self.raw_shape = shape
        self.n_modules = shape[0]
        self.n_asics = n_asics
        self.asics_shape = asics_shape
        self.ss_size = shape[1] // asics_shape[0]
        self.fs_size = shape[2] // asics_shape[1]
        self.pixel_size = pixel_size
        super().__init__(pixel1=pixel_size, pixel2=pixel_size, max_shape=(self.n_modules * asics_shape[0] * self.ss_size, asics_shape[1] * self.fs_size), **kwargs)

class Jungfrau1M(Detector):
    """
    PyFAI Detector instance for the Jungfrau1M
    """

    def __init__(
        self,
        pixel_size=None,
        shape=None,
        n_asics=8,
        asics_shape=(2, 4), # (rows, cols) = (ss, fs)
        **kwargs,
    ):
        if pixel_size is None:
            pixel_size = 0.000075
        if shape is None:
            shape = (4, 512, 1024)
        self.raw_shape = shape
        self.n_modules = shape[0]
        self.n_asics = n_asics
        self.asics_shape = asics_shape
        self.ss_size = shape[1] // asics_shape[0]
        self.fs_size = shape[2] // asics_shape[1]
        self.pixel_size = pixel_size
        super().__init__(pixel1=pixel_size, pixel2=pixel_size, max_shape=(self.n_modules * asics_shape[0] * self.ss_size, asics_shape[1] * self.fs_size), **kwargs)

class Jungfrau4M(Detector):
    """
    PyFAI Detector instance for the Jungfrau4M
    """

    def __init__(
        self,
        pixel_size=None,
        shape=None,
        n_asics=8,
        asics_shape=(2, 4), # (rows, cols) = (ss, fs)
        **kwargs,
    ):
        if pixel_size is None:
            pixel_size = 0.000075
        if shape is None:
            shape = (8, 512, 1024)
        self.raw_shape = shape
        self.n_modules = shape[0]
        self.n_asics = n_asics
        self.asics_shape = asics_shape
        self.ss_size = shape[1] // asics_shape[0]
        self.fs_size = shape[2] // asics_shape[1]
        self.pixel_size = pixel_size
        super().__init__(pixel1=pixel_size, pixel2=pixel_size, max_shape=(self.n_modules * asics_shape[0] * self.ss_size, asics_shape[1] * self.fs_size), **kwargs)

class Rayonix(Detector):
    """
    PyFAI Detector instance for the Rayonix
    By default, the Rayonix detector is defined unbinned. The user can specify the pixel size and detector shape to bin the detector if wanted.
    """

    def __init__(
        self,
        pixel_size=None,
        shape=None,
        n_asics=1,
        asics_shape=(1, 1),
        **kwargs,
    ):
        if pixel_size is None:
            pixel_size = 0.000176
        if shape is None:
            shape = (1920, 1920)
        self.raw_shape = shape
        self.n_modules = 1
        self.n_asics = n_asics
        self.asics_shape = asics_shape
        self.ss_size = shape[0] // asics_shape[0]
        self.fs_size = shape[1] // asics_shape[1]
        self.pixel_size = pixel_size
        super().__init__(pixel1=pixel_size, pixel2=pixel_size, max_shape=(self.n_modules * asics_shape[0] * self.ss_size, asics_shape[1] * self.fs_size), **kwargs)

class PsanaToCrystFEL:
    """
    Class to convert psana .data geometry files to CrystFEL .geom geometry files in the desired reference frame

    Parameters
    ----------
    in_file : str
        Path to the psana .data file
    out_file : str
        Path to the output CrystFEL .geom file
    cframe : int
        Frame reference to convert to CrystFEL format
        0 = psana frame, 1 = lab frame
    """

    def __init__(self, in_file, out_file, cframe=gu.CFRAME_PSANA):
        self.convert_data_to_geom(in_file=in_file, out_file=out_file, cframe=cframe)

    def convert_data_to_geom(self, in_file, out_file, cframe=gu.CFRAME_PSANA):
        """
        Write a CrystFEL .geom file from a psana .data file using PSCalib.UtilsConvert functions
        """
        geo = GeometryAccess(path=in_file, pbits=0, use_wide_pix_center=False)
        top = geo.get_top_geo()
        child = top.get_list_of_children()[0]
        x, y, z = geo.get_pixel_coords(oname=child.oname, oindex=0, do_tilt=True, cframe=cframe)
        geo1 = geo.get_seg_geo() # GeometryObject
        seg = geo1.algo # object of the SegmentGeometry subclass
        nsegs = int(x.size/seg.size())
        shape = (nsegs,) + seg.shape() # (nsegs, srows, scols)
        x.shape = shape
        y.shape = shape
        z.shape = shape
        txt = header_crystfel()
        for n in range(nsegs):
            txt += panel_constants_to_crystfel(seg, n, x[n,:], y[n,:], z[n,:])
        if out_file is not None:
            f = open(out_file,'w')
            f.write(txt)
            f.close()

class CrystFELToPyFAI:
    """
    Class to convert a CrystFEL .geom geometry file to a PyFAI Detector instance with the correct pixel corners

    Parameters
    ----------
    in_file : str
        Path to the CrystFEL .geom file
    det_type : str
        Detector type
    pixel_size : float
        Pixel size in meters
    shape : tuple
        Detector shape (n_modules, ss_size, fs_size)
    cframe : int
        Frame reference to convert to PyFAI format
        0 = psana frame, 1 = lab frame
    """

    def __init__(self, in_file, det_type, pixel_size=None, shape=None):
        self.detector = get_detector(det_type=det_type, pixel_size=pixel_size, shape=shape)
        parser = self.parse_CrystFEL(in_file=in_file)
        pix_pos = self.get_pixel_coordinates(parser=parser)
        corner_array = self.get_pixel_corners(pix_pos=pix_pos, parser=parser)
        self.detector.set_pixel_corners(ary=corner_array)

    def parse_CrystFEL(self, in_file: str):
        """
        Parse a CrystFEL geometry file
        Read a text ".geom" file and return the dictionary of geometry components

        Parameters
        ----------
        fname : str
            Path to the CrystFEL geometry file
        """
        parser = {
            "panels": {},
            "rigid_groups": {},
            "rigid_group_collections": {},
        }
        with open(in_file, "r") as f:
            lines = f.readlines()
            for line in lines:
                # Remove comments
                if line[0] == ";":
                    continue
                if "=" not in line:
                    continue
                fmt_line = line.strip()
                # CrystFEL fmt: object = val
                obj_value = line.split("=")
                # object fmt: "panel/parameter"
                obj = obj_value[0].split("/")  # May be len 1 or 2
                value = obj_value[1]
                if len(obj) == 1:  # e.g. rigid_group_quad ...
                    if "collection" in obj[0].strip():
                        collection_name = obj[0].strip().split("_")[-1]
                        parser["rigid_group_collections"][
                            collection_name
                        ] = value.strip().split(",")
                    else:
                        group_name = obj[0].strip().split("_")[-1]
                        parser["rigid_groups"][group_name] = value.strip().split(",")
                elif len(obj) == 2:  # e.g. p0a0/fs = ...
                    pname = obj[0].strip()
                    if pname in parser["panels"]:
                        panel = parser["panels"][pname]
                    else:
                        panel = {
                            "fs": (0, 0, 0),
                            "ss": (0, 0, 0),
                            "res": 10000,
                            "corner_x": 100,
                            "corner_y": 100,
                            "coffset": 0.1,
                            "min_fs": 0,
                            "max_fs": 123,
                            "min_ss": 0,
                            "max_ss": 123,
                            "no_index": 0,
                        }
                        parser["panels"][pname] = panel
                    if "fs" in obj[1].strip()[-2:]:
                        if "max" in obj[1]:
                            panel["max_fs"] = int(value)
                        elif "min" in obj[1]:
                            panel["min_fs"] = int(value)
                        else:
                            strcoords = value.split()
                            if "z" in strcoords:
                                # -1x -2y -3z
                                fcoords = (
                                    float(strcoords[0].strip("x")),
                                    float(strcoords[1].strip("y")),
                                    float(strcoords[2].strip("z")),
                                )
                                panel["fs"] = fcoords
                            else:
                                # -1x -2y
                                fcoords = (
                                    float(strcoords[0].strip("x")),
                                    float(strcoords[1].strip("y")),
                                    0.0,
                                )
                                panel["fs"] = fcoords
                    elif "ss" in obj[1].strip()[-2:]:
                        if "max" in obj[1]:
                            panel["max_ss"] = int(value)
                        elif "min" in obj[1]:
                            panel["min_ss"] = int(value)
                        else:
                            strcoords = value.split()
                            if "z" in strcoords:
                                # -1x -2y -3z
                                fcoords = (
                                    float(strcoords[0].strip("x")),
                                    float(strcoords[1].strip("y")),
                                    float(strcoords[2].strip("z")),
                                )
                                panel["ss"] = fcoords
                            else:
                                # -1x -2y
                                fcoords = (
                                    float(strcoords[0].strip("x")),
                                    float(strcoords[1].strip("y")),
                                    0.0,
                                )
                                panel["ss"] = fcoords
                    elif "res" in obj[1].strip():
                        panel["res"] = float(value)
                    elif "corner" in obj[1].strip():
                        if "x" in obj[1]:
                            panel["corner_x"] = float(value)
                        elif "y" in obj[1]:
                            panel["corner_y"] = float(value)
                    elif "no_index" in obj[1]:
                        panel["no_index"] = int(value)
                    elif "coffset" in obj[1]:
                        panel["coffset"] = float(value)
            return parser

    def get_pixel_coordinates(self, parser: dict):
        """
        From a parsed CrystFEL .geom file, returns the pixel positions

        Parameters
        ----------
        parser : dict
            Dictionary of geometry inputs from a CrystFEL geometry file
        """
        nmods = self.detector.n_modules
        nasics = self.detector.n_asics
        asics_shape = self.detector.asics_shape
        fs_size = self.detector.fs_size
        ss_size = self.detector.ss_size
        pix_pos = np.zeros([nmods, ss_size * asics_shape[0], fs_size * asics_shape[1], 3])
        for p in range(nmods):
            pname = f"p{p}"
            for asic in range(nasics):
                asicname = f"a{asic}"
                full_name = pname + asicname
                if nasics == 1:
                    arow = 0
                    acol = 0
                else:
                    arow = asic // (nasics//2)
                    acol = asic % (nasics//2)
                ss_portion = slice(arow * ss_size, (arow + 1) * ss_size)
                fs_portion = slice(acol * fs_size, (acol + 1) * fs_size)
                res = parser["panels"][full_name]["res"]
                corner_x = parser["panels"][full_name]["corner_x"] / res
                corner_y = parser["panels"][full_name]["corner_y"] / res
                corner_z = parser["panels"][full_name]["coffset"]
                # Get tile vectors for ss and fs directions
                ssx, ssy, ssz = np.array(parser["panels"][full_name]["ss"]) / res
                fsx, fsy, fsz = np.array(parser["panels"][full_name]["fs"]) / res
                coords_ss, coords_fs = np.meshgrid(
                    np.arange(0, ss_size), np.arange(0, fs_size), indexing="ij"
                )
                x = corner_x + ssx * coords_ss + fsx * coords_fs
                y = corner_y + ssy * coords_ss + fsy * coords_fs
                z = corner_z + ssz * coords_ss + fsz * coords_fs
                pix_pos[p, ss_portion, fs_portion, 0] = x
                pix_pos[p, ss_portion, fs_portion, 1] = y
                pix_pos[p, ss_portion, fs_portion, 2] = z
        if len(np.unique(pix_pos[:, :, :, 2])) == 1:
            pix_pos[:, :, :, 2] = 0
        else:
            pix_pos[:, :, :, 2] -= np.mean(pix_pos[:, :, :, 2])
        return pix_pos

    def get_pixel_corners(self, pix_pos, parser):
        """
        Convert to the corner array needed by PyFAI

        Parameters
        ----------
        pix_pos : np.ndarray
            Pixel positions in .geom reference frame
        parser : dict
            Dictionary of geometry inputs from a CrystFEL geometry file
        cframe : int
            Frame reference to convert to PyFAI format
            0 = psana frame, 1 = lab frame
        """
        nmods = self.detector.n_modules
        nasics = self.detector.n_asics
        asics_shape = self.detector.asics_shape
        fs_size = self.detector.fs_size
        ss_size = self.detector.ss_size
        pixcorner = pix_pos.reshape(nmods * ss_size * asics_shape[0], fs_size * asics_shape[1], 3)
        cx, cy, cz = np.moveaxis(pixcorner, -1, 0)
        # Flattened ss dim, fs dim, 4 corners, 3 coordinates (x, y, z)
        pyfai_fmt = np.zeros([nmods * ss_size * asics_shape[0], fs_size * asics_shape[1], 4, 3])
        for p in range(nmods):
            pname = f"p{p}"
            for asic in range(nasics):
                full_name = f"{pname}a{asic}"
                if nasics == 1:
                    arow = 0
                    acol = 0
                else:
                    arow = asic // (nasics//2)
                    acol = asic % (nasics//2)
                slab_offset = p * asics_shape[0] *ss_size
                ss_portion = slice(
                    arow * ss_size + slab_offset, (arow + 1) * ss_size + slab_offset
                )
                fs_portion = slice(acol * fs_size, (acol + 1) * fs_size)
                # Get tile vectors for ss and fs directions
                res = parser["panels"][full_name]["res"]
                ssx, ssy, ssz = np.array(parser["panels"][full_name]["ss"]) / res
                fsx, fsy, fsz = np.array(parser["panels"][full_name]["fs"]) / res
                c1x = cx[ss_portion, fs_portion]
                c1y = cy[ss_portion, fs_portion]
                c1z = cz[ss_portion, fs_portion]
                ss_units = np.array([0, 1, 1, 0])
                fs_units = np.array([0, 0, 1, 1])
                x = c1x[:, :, np.newaxis] + ss_units * ssx + fs_units * fsx
                y = c1y[:, :, np.newaxis] + ss_units * ssy + fs_units * fsy
                z = c1z[:, :, np.newaxis] + ss_units * ssz + fs_units * fsz
                pyfai_fmt[ss_portion, fs_portion, :, 0] = z
                pyfai_fmt[ss_portion, fs_portion, :, 1] = y
                pyfai_fmt[ss_portion, fs_portion, :, 2] = x
        return pyfai_fmt

class PsanaToPyFAI:
    """
    Class to convert psana .data geometry files directly to PyFAI corner arrays
    bypassing the writing CrystFEL .geom file step

    Parameters
    ----------
    in_file : str
        Path to the psana .data file
    det_type : str
        Detector type
    pixel_size : float
        Pixel size in meters
    shape : tuple
        Detector shape (n_modules, ss_size, fs_size)
    """
    
    def __init__(self, in_file, det_type, pixel_size=None, shape=None):
        self.detector = get_detector(det_type=det_type, pixel_size=pixel_size, shape=shape)
        corner_array = self.get_pixel_corners(in_file=in_file)
        self.detector.set_pixel_corners(ary=corner_array)
    
    def psana_to_pyfai(self, x, y, z):
        """
        Convert psana coordinates to pyfai coordinates
        """
        x = x * 1e-6
        y = y * 1e-6
        z = z * 1e-6
        if len(np.unique(z))==1:
            z = np.zeros_like(z)
        else:
            z -= np.mean(z)
        return -x, y, -z

    def get_pixel_corners(self, in_file):
        geo = GeometryAccess(path=in_file, pbits=0, use_wide_pix_center=False)
        top = geo.get_top_geo()
        child = top.get_list_of_children()[0]
        x, y, z = geo.get_pixel_coords(oname=child.oname, oindex=0, do_tilt=True)
        x, y, z = self.psana_to_pyfai(x, y, z)
        npanels = self.detector.n_modules
        nasics = self.detector.n_asics
        asics_shape = self.detector.asics_shape
        fs_size = self.detector.fs_size
        ss_size = self.detector.ss_size
        pixel_corners = np.zeros([npanels * ss_size * asics_shape[0], fs_size * asics_shape[1], 4, 3])
        for p in range(npanels):
            xp = x[p, :]
            yp = y[p, :]
            zp = z[p, :]
            vfs = np.array((\
                xp[0, fs_size * asics_shape[1] - 1] - xp[0, 0],\
                yp[0, fs_size * asics_shape[1] - 1] - yp[0, 0],\
                zp[0, fs_size * asics_shape[1] - 1] - zp[0, 0]))
            vss = np.array((\
                xp[ss_size * asics_shape[0] - 1,0] - xp[0, 0],\
                yp[ss_size * asics_shape[0] - 1,0] - yp[0, 0],\
                zp[ss_size * asics_shape[0] - 1,0] - zp[0, 0]))
            nfs = vfs / np.linalg.norm(vfs)
            nss = vss / np.linalg.norm(vss)
            for a in range(nasics):
                if nasics == 1:
                    arow = 0
                    acol = 0
                else:
                    arow = a // (nasics//2)
                    acol = a % (nasics//2)
                fs_portion = slice(acol * fs_size, (acol + 1) * fs_size)
                ss_portion = slice(arow * ss_size, (arow + 1) * ss_size)
                slab_offset = p * asics_shape[0] * ss_size
                fs_portion_slab = slice(acol * fs_size, (acol + 1) * fs_size)
                ss_portion_slab = slice(arow * ss_size + slab_offset, (arow + 1) * ss_size + slab_offset)
                ssx, ssy, ssz = nss * self.detector.pixel1
                fsx, fsy, fsz = nfs * self.detector.pixel2
                xasic = x[p, ss_portion, fs_portion]
                yasic = y[p, ss_portion, fs_portion]
                zasic = z[p, ss_portion, fs_portion]
                corner_ss = np.array([0, 0, 1, 1])
                corner_fs = np.array([0, 1, 1, 0])
                xasic = xasic[:, :, np.newaxis] + corner_ss * ssx + corner_fs * fsx
                yasic = yasic[:, :, np.newaxis] + corner_ss * ssy + corner_fs * fsy
                zasic = zasic[:, :, np.newaxis] + corner_ss * ssz + corner_fs * fsz
                pixel_corners[ss_portion_slab, fs_portion_slab, :, 0] = zasic
                pixel_corners[ss_portion_slab, fs_portion_slab, :, 1] = xasic
                pixel_corners[ss_portion_slab, fs_portion_slab, :, 2] = yasic
        return pixel_corners

class PyFAIToCrystFEL:
    """
    Class to write CrystFEL .geom geometry files from PyFAI SingleGeometry instance

    Parameters
    ----------
    detector : PyFAI detector instance
        PyFAI detector instance
    params : list
        Detector parameters found by PyFAI calibration
    psana_file : str
        Path to the psana .data file for retrieving segmentation information
    out_file : str
        Path to the output .geom file
    """

    def __init__(self, detector, params, psana_file, out_file):
        self.detector = detector
        self.params = params
        self.correct_geom()
        self.convert_to_geom(psana_file=psana_file, out_file=out_file)

    def rotation_matrix(self, params):
        """
        Compute and return the detector tilts as a single rotation matrix

        Parameters
        ----------
        params : list
            Detector parameters found by PyFAI calibration
        """
        if params is None:
            params = self.params
        cos_rot1 = np.cos(params[3])
        cos_rot2 = np.cos(params[4])
        cos_rot3 = np.cos(params[5])
        sin_rot1 = np.sin(params[3])
        sin_rot2 = np.sin(params[4])
        sin_rot3 = np.sin(params[5])
        # Rotation about axis 1: Note this rotation is left-handed
        rot1 = np.array([[1.0, 0.0, 0.0],
                            [0.0, cos_rot1, sin_rot1],
                            [0.0, -sin_rot1, cos_rot1]])
        # Rotation about axis 2. Note this rotation is left-handed
        rot2 = np.array([[cos_rot2, 0.0, -sin_rot2],
                            [0.0, 1.0, 0.0],
                            [sin_rot2, 0.0, cos_rot2]])
        # Rotation about axis 3: Note this rotation is right-handed
        rot3 = np.array([[cos_rot3, -sin_rot3, 0.0],
                            [sin_rot3, cos_rot3, 0.0],
                            [0.0, 0.0, 1.0]])
        rotation_matrix = np.dot(np.dot(rot3, rot2), rot1)  # 3x3 matrix
        return rotation_matrix
    
    def pyfai_to_psana(self, x, y, z, params):
        """
        Convert back to psana coordinates

        Parameters
        ----------
        x : np.ndarray
            X coordinate in meters
        y : np.ndarray
            Y coordinate in meters
        z : np.ndarray
            Z coordinate in meters
        params : list
            Detector parameters found by PyFAI calibration
        """
        z -= np.mean(z)
        if params is None:
            params = self.params
        cos_rot1 = np.cos(params[3])
        cos_rot2 = np.cos(params[4])
        distance_sample_detector = params[0]*(1/(cos_rot1*cos_rot2))
        z += distance_sample_detector
        x, y, z = x*1e6, y*1e6, z*1e6
        return -x, y, -z

    def correct_geom(self):
        """
        Correct the geometry based on the given parameters found by PyFAI calibration
        Finally scale to micrometers (needed for writing CrystFEL .geom files)
        """
        params = self.params
        p1, p2, p3 = self.detector.calc_cartesian_positions()
        dist = self.params[0]
        poni1 = self.params[1]
        poni2 = self.params[2]
        p1 = (p1 - poni1).ravel()
        p2 = (p2 - poni2).ravel()
        if p3 is None:
            p3 = np.zeros_like(p1) + dist
        else:
            p3 = (p3+dist).ravel()
        coord_det = np.vstack((p1, p2, p3))
        coord_sample = np.dot(self.rotation_matrix(params), coord_det)
        x, y, z = coord_sample
        x, y, z = self.pyfai_to_psana(x, y, z, params)
        X = np.reshape(x, (self.detector.n_modules, self.detector.ss_size * self.detector.asics_shape[0], self.detector.fs_size * self.detector.asics_shape[1]))
        Y = np.reshape(y, (self.detector.n_modules, self.detector.ss_size * self.detector.asics_shape[0], self.detector.fs_size * self.detector.asics_shape[1]))
        Z = np.reshape(z, (self.detector.n_modules, self.detector.ss_size * self.detector.asics_shape[0], self.detector.fs_size * self.detector.asics_shape[1]))
        self.X = X
        self.Y = Y
        self.Z = Z
    
    def convert_to_geom(self, psana_file, out_file):
        """
        From corrected X, Y, Z coordinates, write a CrystFEL .geom file

        Parameters
        ----------
        psana_file : str
            Path to the psana .data file for retrieving segmentation information
        output_file : str
            Path to the output .geom file
        """
        X, Y, Z = self.X, self.Y, self.Z
        geom = GeometryAccess(path=psana_file, pbits=0, use_wide_pix_center=False)
        geom1 = geom.get_seg_geo() # GeometryObject
        seg = geom1.algo # object of the SegmentGeometry subclass
        nsegs = int(X.size/seg.size())
        shape = (nsegs,) + seg.shape() # (nsegs, srows, scols)
        X.shape = shape
        Y.shape = shape
        Z.shape = shape
        txt = header_crystfel()
        for n in range(nsegs):
            txt += panel_constants_to_crystfel(seg, n, X[n,:], Y[n,:], Z[n,:])
        if out_file is not None:
            f = open(out_file,'w')
            f.write(txt)
            f.close()

class CrystFELToPsana:
    """
    Class to convert CrystFEL .geom geometry files to psana .data geometry files thanks to det_type information

    Parameters
    ----------
    in_file : str
        Path to the CrystFEL .geom file
    det_type : str
        Detector type
    out_file : str
        Path to the output psana .data file
    pixel_size : float
        Pixel size in meters
    shape : tuple
        Detector shape (n_modules, ss_size, fs_size)
    """
    def __init__(self, in_file, det_type, out_file, pixel_size=None, shape=None):
        self.valid = False
        self.load_geom(in_file=in_file)
        self.convert_geom_to_data(det_type=det_type, out_file=out_file, pixel_size=pixel_size, shape=shape)

    @staticmethod
    def str_to_int_or_float(s):
        v = float(s)
        if v%1 == 0: v=int(v)
        return v

    @staticmethod
    def sfields_to_xyz_vector(flds):
        """ 
        converts ['+0.002583x', '-0.999997y', '+0.000000z'] to (0.002583, -0.999997, 0.000000)
        """
        v = (float(flds[0].strip('x')), float(flds[1].strip('y')))
        z = float(flds[2].strip('z')) if len(flds)==3 else 0
        v += (z,)
        return v

    @staticmethod
    def angle_and_tilt(a):
        """
        for angle in range [-180,180] returns nearest design angle and tilt.
        output angle range is shifted to positive [0,360]
        """
        desangles = np.array((-180,-90, 0, 90, 180))
        difangles = a-desangles
        absdifang = np.absolute(difangles)
        imin = np.where(absdifang == np.amin(absdifang))[0]
        angle, tilt = desangles[imin], difangles[imin]
        return (angle if angle>=0 else angle+360), tilt

    @staticmethod
    def unit_vector_pitch_angle_max_ind(u):
        """
        unit vector pitch (axis transverse direction in x-y plane) angle
        """
        absu = np.absolute(u)
        imax = np.where(absu == np.amax(absu))[0]
        pitch = degrees(atan2(u[2],u[imax]))
        pitch = (pitch+180) if pitch<-90 else (pitch-180) if pitch>90 else pitch
        return pitch, imax

    @staticmethod
    def vector_lab_to_psana(v):
        """
        both-way conversion of vectors between LAB and PSANA coordinate frames
        """
        assert len(v)==3
        return np.array((-v[1], -v[0], -v[2]))

    @staticmethod
    def tilt_xy(uf, us, i, k):
        tilt_f, imaxf = CrystFELToPsana.unit_vector_pitch_angle_max_ind(uf)
        tilt_s, imaxs = CrystFELToPsana.unit_vector_pitch_angle_max_ind(us)
        tilt_x, tilt_y = (tilt_s, tilt_f) if imaxf==0 else (tilt_f, tilt_s)
        return tilt_x, -tilt_y

    @staticmethod
    def str_is_segment_and_asic(s):
        """ 
        check if s looks like str 'q0a2' or 'p12a7'
        returns 'p0.2' or 'p12.7' or False
        """
        if not isinstance(s, str)\
        or len(s)<2: return False
        flds = s[1:].split('a')
        return False if len(flds) !=2 else\
            'p%sa%s' % (flds[0], flds[1]) if all([f.isdigit() for f in flds]) else\
            False

    @staticmethod
    def header_psana(list_of_cmts=[], det_type='N/A'):
        comments = '\n'.join(['# CFELCMT:%02d %s'%(i,s) for i,s in enumerate(list_of_cmts)])
        return\
        '\n# TITLE      Geometry constants converted from CrystFEL by genuine psana'\
        +'\n# DATE_TIME  %s' % gu.str_tstamp(fmt='%Y-%m-%dT%H:%M:%S %Z')\
        +'\n# AUTHOR     %s' % gu.get_login()\
        +'\n# CWD        %s' % gu.get_cwd()\
        +'\n# HOST       %s' % gu.get_hostname()\
        +'\n# COMMAND    %s' % ' '.join(sys.argv)\
        +'\n# RELEASE    %s' % gu.get_enviroment('CONDA_DEFAULT_ENV')\
        +'\n# CALIB_TYPE geometry'\
        +'\n# DETTYPE    %s' % det_type\
        +'\n# DETECTOR   N/A'\
        '\n# METROLOGY  N/A'\
        '\n# EXPERIMENT N/A'\
        +'\n%s' % comments\
        +'\n#'\
        '\n# HDR PARENT IND        OBJECT IND     X0[um]   Y0[um]   Z0[um]   ROT-Z ROT-Y ROT-X     TILT-Z   TILT-Y   TILT-X'

    def _parse_line_as_parameter(self, line):
        assert isinstance(line, str), 'line is not a str object'
        fields = line.split()
        nfields = len(fields)
        if fields[1] != '=':
            self.list_of_ignored_records.append(line)
            return
        keys = fields[0].split('/') # ex: p15a3/corner_y
        nkeys = len(keys)
        if nkeys==1:
            if nfields>3:
                self.list_of_ignored_records.append(line)
                return
            k0 = keys[0]
            self.dict_of_pars[k0] = float(fields[2]) if k0 in ('res', 'adu_per_eV', 'coffset') else\
                ' '.join(fields[2:])
        elif nkeys==2:
            k0, k1 = keys
            resp = CrystFELToPsana.str_is_segment_and_asic(k0)
            if resp: k0=resp
            v = '' if nfields<3 else\
                CrystFELToPsana.sfields_to_xyz_vector(fields[2:]) if k1 in ('fs','ss') else\
                int(fields[2]) if k1 in ('max_ss', 'min_ss', 'max_fs', 'min_fs', 'no_index') else\
                int(fields[2]) if k1 in ('max_x', 'min_x', 'max_y', 'min_y') else\
                float(fields[2]) if k1 in ('res', 'corner_x', 'corner_y', 'adu_per_eV', 'coffset') else\
                float(fields[2]) if k1 in ('xfs', 'yfs', 'xss', 'yss') else\
                ' '.join(fields[2:]) # str_to_int_or_float(fields[2])
            if k0 in self.dict_of_pars.keys():
                self.dict_of_pars[k0][k1] = v
            else:
                self.dict_of_pars[k0] = {k1:v,}
        else:
            self.list_of_ignored_records.append(line)
            return

    def str_list_of_comments(self):
        return 'List of comments\n'\
            + '\n'.join(self.list_of_comments)

    def str_list_of_ignored_records(self):
        return 'List of ignored records\n'\
            + '\n'.join(self.list_of_ignored_records)

    def str_dict_of_pars(self):
        keys = sorted(self.dict_of_pars.keys())
        msg = 'dict of parameters with top keys: %s' % ' '.join(keys)
        for k in keys:
            v = self.dict_of_pars[k]
            if isinstance(v,dict):
                msg += '\n%s: %s' % (k, CrystFELToPsana.str_is_segment_and_asic(k))
                for k2,v2 in v.items(): msg += '\n    %s: %s' % (k2,v2)
            else: msg += '\n%s: %s' % (k,v)
        return msg

    def load_geom(self, in_file):
        self.valid = False
        self.list_of_comments = []
        self.list_of_ignored_records = []
        self.dict_of_pars = {}
        f=open(in_file,'r')
        for linef in f:
            line = linef.strip('\n')
            if not line.strip(): continue # discard empty strings
            if line[0] == ';':            # accumulate list of comments
                self.list_of_comments.append(line)
                continue
            self._parse_line_as_parameter(line)
        f.close()
        self.valid = True

    def geom_to_data(self, pars, det_type, out_file):
        segname, panasics = pars
        sg = sgs.Create(segname=segname, pbits=0, use_wide_pix_center=False)
        X,Y,Z = sg.pixel_coord_array()
        PIX_SIZE_UM = sg.get_pix_size_um()
        M_TO_UM = 1e6
        xc0, yc0, zc0 = X[0,0], Y[0,0], Z[0,0]
        zoffset_m = self.dict_of_pars.get('coffset', 0) # in meters
        recs = CrystFELToPsana.header_psana(list_of_cmts=self.list_of_comments, det_type=det_type)
        segz = np.array([self.dict_of_pars[k].get('coffset', 0) for k in panasics.split(',')])
        meanroundz = round(segz.mean()*1e6)*1e-6 # round z to 1µm
        zoffset_m += meanroundz
        for i,k in enumerate(panasics.split(',')):
            dicasic = self.dict_of_pars[k]
            uf = np.array(dicasic.get('fs', None), dtype=np.float64) # unit vector f
            us = np.array(dicasic.get('ss', None), dtype=np.float64) # unit vector s
            vf = uf*abs(xc0)
            vs = us*abs(yc0)
            x0pix = dicasic.get('corner_x', 0) # The units are pixel widths of the current panel
            y0pix = dicasic.get('corner_y', 0)
            z0m   = dicasic.get('coffset', 0)
            v00center = vf + vs
            v00corner = np.array((x0pix*PIX_SIZE_UM, y0pix*PIX_SIZE_UM, (z0m - zoffset_m)*M_TO_UM))
            vcent = v00corner + v00center
            angle_deg = degrees(atan2(uf[1],uf[0]))
            angle_z, tilt_z = CrystFELToPsana.angle_and_tilt(angle_deg)
            tilt_x, tilt_y = CrystFELToPsana.tilt_xy(uf,us,i,k)
            recs += '\nDET:VC         0  %12s  %2d' % (segname, i)\
                + '   %8d %8d %8d %7.0f     0     0   %8.5f %8.5f %8.5f'%\
                (vcent[0], vcent[1], vcent[2], angle_z, tilt_z, tilt_y, tilt_x)
        recs += '\nIP             0    DET:VC       0          0        0'\
                ' %8d       0     0     0    0.00000  0.00000  0.00000' % (zoffset_m*M_TO_UM)
        f=open(out_file,'w')
        f.write(recs)
        f.close()

    def convert_geom_to_data(self, det_type, out_file, pixel_size=None, shape=None):
        det_type = det_type.lower()
        if "epix10kaquad" in det_type:
            det_type = "epix10kaquad"
        elif det_type == "rayonix":
            pixel_size_um = pixel_size*1e6
            pars = (f'MTRX:V2:{shape[0]}:{shape[1]}:{int(pixel_size_um)}:{int(pixel_size_um)}', 'p0a0')
        else:
            pars = DETTYPE_TO_PARS.get(det_type, None)
        self.geom_to_data(pars, det_type, out_file)