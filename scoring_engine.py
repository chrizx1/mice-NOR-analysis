from pathlib import Path
from tkinter import Tk, filedialog, simpledialog, messagebox
from typing import Optional
from collections import deque
import json
import math

import cv2
import numpy as np
import pandas as pd
from scipy.ndimage import distance_transform_edt
from skimage.morphology import skeletonize
from immobility import flag_frames, flag_bouts, exclude_immobility

IMMOBILITY_DURATION_SECONDS = 10.0
IMMOBILITY_TOLERANCE_PX = 2.0


# ============================================================
# PROJECT PATHS
# ============================================================

PROJECT_FOLDER = Path(__file__).resolve().parent.parent
OUTPUT_FOLDER = PROJECT_FOLDER / "output"
CALIBRATION_FOLDER = PROJECT_FOLDER / "calibration"


# ============================================================
# INVESTIGATION SETTINGS
# ============================================================

# Object radius = r
# Investigation-zone radius = 4r
#
# Therefore:
# maximum nose-to-object-edge distance = 3r
INVESTIGATION_RADIUS_MULTIPLIER = 4.0

MAXIMUM_HEAD_ANGLE_DEGREES = 90.0

MINIMUM_BOUT_DURATION_SECONDS = 0.20

MAXIMUM_GAP_SECONDS = 0.20

ALLOW_UNCERTAIN_ORIENTATION_FOR_SCORING = False


# ============================================================
# OBJECT CALIBRATION QC
# ============================================================

OBJECT_RADIUS_QC_WARNING_PERCENT = 10.0


# ============================================================
# MOUSE DETECTION SETTINGS
# ============================================================

DIFFERENCE_THRESHOLD = 25

MINIMUM_MOUSE_AREA = 150
MAXIMUM_MOUSE_AREA = 7000

BACKGROUND_SAMPLE_COUNT = 120

OPEN_KERNEL_SIZE = 3
CLOSE_KERNEL_SIZE = 9
CLOSE_ITERATIONS = 2


# ============================================================
# SKELETON SETTINGS
# ============================================================

MINIMUM_BRANCH_LENGTH_PIXELS = 6.0
ENDPOINT_THICKNESS_SAMPLE_LENGTH = 12


# ============================================================
# TAIL SCORING
# ============================================================

TAIL_THICKNESS_WEIGHT = 5.0
TAIL_BRANCH_LENGTH_WEIGHT = 0.30
TAIL_CONTINUITY_WEIGHT = 0.35
TAIL_CONFIDENCE_MARGIN = 3.0


# ============================================================
# HEAD SCORING
# ============================================================

HEAD_THICKNESS_WEIGHT = 6.0
HEAD_TAIL_DISTANCE_WEIGHT = 0.15
HEAD_CONTINUITY_WEIGHT = 0.35
HEAD_BRANCH_LENGTH_WEIGHT = 0.20
HEAD_CONFIDENCE_MARGIN = 3.0


# ============================================================
# TEMPORAL SETTINGS
# ============================================================

SMOOTHING_ALPHA = 0.80

MAXIMUM_UNCERTAIN_FRAMES_TO_HOLD = 4

MAXIMUM_MISSED_FRAMES_BEFORE_RESET = 10


# ============================================================
# USER INTERFACE
# ============================================================

def create_hidden_root():

    root = Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    return root


def select_video_file() -> Path:

    root = create_hidden_root()

    video_file = filedialog.askopenfilename(
        title="Select mouse behavior video",
        initialdir=str(PROJECT_FOLDER / "video"),
        filetypes=[
            ("Video files", "*.mp4 *.avi *.mov *.mkv"),
            ("MP4 files", "*.mp4"),
            ("AVI files", "*.avi"),
            ("All files", "*.*"),
        ],
        parent=root,
    )

    root.destroy()

    if not video_file:
        raise RuntimeError(
            "No video was selected."
        )

    return Path(video_file)


def ask_analysis_duration_minutes() -> float:

    root = create_hidden_root()

    duration_minutes = simpledialog.askfloat(
        "Analysis duration",
        "Enter analysis duration in minutes:\n\n"
        "Examples:\n"
        "10 = first 10 minutes\n"
        "5 = first 5 minutes",
        initialvalue=10.0,
        minvalue=0.01,
        parent=root,
    )

    root.destroy()

    if duration_minutes is None:
        raise RuntimeError(
            "Analysis duration was not entered."
        )

    return duration_minutes


def ask_session_type() -> str:

    while True:

        root = create_hidden_root()

        answer = simpledialog.askstring(
            "Session type",
            "Enter session type:\n\n"
            "encoding = create/save object-radius calibration\n"
            "test = use an existing encoding calibration",
            initialvalue="encoding",
            parent=root,
        )

        root.destroy()

        if answer is None:
            raise RuntimeError(
                "Session type was not selected."
            )

        answer = answer.strip().lower()

        if answer in (
            "encoding",
            "encode",
            "e",
        ):
            return "encoding"

        if answer in (
            "test",
            "testing",
            "t",
        ):
            return "test"

        root = create_hidden_root()

        messagebox.showerror(
            "Invalid session type",
            "Please enter either:\n\n"
            "encoding\n"
            "or\n"
            "test",
            parent=root,
        )

        root.destroy()


def ask_preference_direction() -> int:

    root = create_hidden_root()

    answer = simpledialog.askinteger(
        "Preference index direction",
        "Which object should be the POSITIVE object?\n\n"
        "1 = (Object 1 - Object 2) / "
        "(Object 1 + Object 2)\n\n"
        "2 = (Object 2 - Object 1) / "
        "(Object 1 + Object 2)\n\n"
        "For NOR, choose the novel object.",
        initialvalue=2,
        minvalue=1,
        maxvalue=2,
        parent=root,
    )

    root.destroy()

    if answer not in (
        1,
        2,
    ):
        raise RuntimeError(
            "Preference-index direction was not selected."
        )

    return answer


def guess_animal_id(
    video_path: Path,
) -> str:

    name = video_path.stem

    suffixes = [
        "_encoding",
        "-encoding",
        " encoding",
        "_encode",
        "_test",
        "-test",
        " test",
        "_testing",
    ]

    lower_name = name.lower()

    for suffix in suffixes:

        if lower_name.endswith(
            suffix.lower()
        ):

            return name[
                :-len(suffix)
            ]

    return name


def ask_animal_id(
    video_path: Path,
) -> str:

    suggested_id = guess_animal_id(
        video_path
    )

    root = create_hidden_root()

    animal_id = simpledialog.askstring(
        "Animal ID",
        "Enter the animal ID.\n\n"
        "Use the same ID for its encoding and test sessions.",
        initialvalue=suggested_id,
        parent=root,
    )

    root.destroy()

    if animal_id is None:

        raise RuntimeError(
            "Animal ID was not entered."
        )

    animal_id = animal_id.strip()

    if not animal_id:

        raise RuntimeError(
            "Animal ID cannot be blank."
        )

    return animal_id


def select_calibration_file() -> Path:

    CALIBRATION_FOLDER.mkdir(
        parents=True,
        exist_ok=True,
    )

    root = create_hidden_root()

    calibration_file = filedialog.askopenfilename(
        title="Select encoding calibration for this animal",
        initialdir=str(CALIBRATION_FOLDER),
        filetypes=[
            ("Calibration JSON", "*.json"),
            ("All files", "*.*"),
        ],
        parent=root,
    )

    root.destroy()

    if not calibration_file:

        raise RuntimeError(
            "No calibration file was selected."
        )

    return Path(
        calibration_file
    )


def duration_label(
    duration_seconds: float,
) -> str:

    minutes = (
        duration_seconds / 60.0
    )

    if (
        abs(
            minutes
            - round(minutes)
        )
        < 1e-8
    ):

        return (
            f"{int(round(minutes))}min"
        )

    return (
        f"{minutes:.2f}"
        .rstrip("0")
        .rstrip(".")
        + "min"
    )


# ============================================================
# ARENA POLYGON SELECTION
# ============================================================

def order_arena_points(
    points: np.ndarray,
) -> np.ndarray:
    """
    Automatically order four clicked points as:

        top-left
        top-right
        bottom-right
        bottom-left

    This makes the selector a little more forgiving if the clicks
    are not perfectly ordered.
    """

    points = np.asarray(
        points,
        dtype=np.float32,
    )

    ordered = np.zeros(
        (4, 2),
        dtype=np.float32,
    )

    sums = points.sum(
        axis=1
    )

    differences = np.diff(
        points,
        axis=1,
    ).reshape(-1)

    ordered[0] = points[
        np.argmin(sums)
    ]

    ordered[2] = points[
        np.argmax(sums)
    ]

    ordered[1] = points[
        np.argmin(differences)
    ]

    ordered[3] = points[
        np.argmax(differences)
    ]

    return ordered


def select_arena_polygon(
    first_frame: np.ndarray,
):
    """
    Click four arena corners.

    Controls:
        Left click = add point
        R          = reset
        Enter      = accept
        Esc        = cancel

    Returns:
        arena_points_full
        arena_roi
        arena_mask_roi
    """

    window_name = (
        "Select arena corners"
    )

    original = (
        first_frame.copy()
    )

    points = []

    def mouse_callback(
        event,
        x,
        y,
        flags,
        param,
    ):

        if (
            event
            == cv2.EVENT_LBUTTONDOWN
            and
            len(points) < 4
        ):

            points.append(
                (x, y)
            )

    cv2.namedWindow(
        window_name,
        cv2.WINDOW_NORMAL,
    )

    cv2.setMouseCallback(
        window_name,
        mouse_callback,
    )

    while True:

        display = (
            original.copy()
        )

        cv2.putText(
            display,
            "Click 4 arena corners",
            (20, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.70,
            (0, 255, 255),
            2,
            cv2.LINE_AA,
        )

        cv2.putText(
            display,
            "Suggested order: TL -> TR -> BR -> BL",
            (20, 58),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )

        cv2.putText(
            display,
            "R = reset | ENTER = accept | ESC = cancel",
            (20, 84),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.50,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )

        # Draw points as clicked.
        for index, point in enumerate(
            points
        ):

            cv2.circle(
                display,
                point,
                7,
                (0, 255, 255),
                -1,
            )

            cv2.putText(
                display,
                str(index + 1),
                (
                    point[0] + 8,
                    point[1] - 8,
                ),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (0, 255, 255),
                2,
                cv2.LINE_AA,
            )

        # Draw temporary lines.
        if len(points) >= 2:

            for index in range(
                len(points) - 1
            ):

                cv2.line(
                    display,
                    points[index],
                    points[index + 1],
                    (0, 255, 255),
                    2,
                )

        # Once four points exist, show the automatically ordered
        # final polygon in green.
        if len(points) == 4:

            ordered = (
                order_arena_points(
                    np.array(points)
                )
            )

            ordered_int = (
                ordered.astype(
                    np.int32
                )
            )

            cv2.polylines(
                display,
                [
                    ordered_int.reshape(
                        -1,
                        1,
                        2,
                    )
                ],
                isClosed=True,
                color=(0, 255, 0),
                thickness=2,
            )

        cv2.imshow(
            window_name,
            display,
        )

        key = (
            cv2.waitKey(20)
            & 0xFF
        )

        # R / r
        if key in (
            ord("r"),
            ord("R"),
        ):

            points.clear()

        # ESC
        elif key == 27:

            cv2.destroyWindow(
                window_name
            )

            raise RuntimeError(
                "Arena selection cancelled."
            )

        # Enter
        elif key in (
            13,
            10,
        ):

            if len(points) != 4:

                print(
                    "Please click exactly 4 arena corners before pressing ENTER."
                )

                continue

            break

    cv2.destroyWindow(
        window_name
    )

    arena_points_full = (
        order_arena_points(
            np.array(
                points,
                dtype=np.float32,
            )
        )
    )

    arena_points_int = (
        arena_points_full.astype(
            np.int32
        )
    )

    # Rectangular bounding box used internally for efficient cropping.
    x, y, width, height = (
        cv2.boundingRect(
            arena_points_int
        )
    )

    arena_roi = (
        x,
        y,
        width,
        height,
    )

    # Convert full-frame polygon coordinates to ROI-local coordinates.
    arena_points_roi = (
        arena_points_full.copy()
    )

    arena_points_roi[:, 0] -= x
    arena_points_roi[:, 1] -= y

    arena_points_roi = (
        arena_points_roi.astype(
            np.int32
        )
    )

    arena_mask_roi = np.zeros(
        (
            height,
            width,
        ),
        dtype=np.uint8,
    )

    cv2.fillPoly(
        arena_mask_roi,
        [
            arena_points_roi.reshape(
                -1,
                1,
                2,
            )
        ],
        255,
    )

    return (
        arena_points_full,
        arena_roi,
        arena_mask_roi,
    )


def draw_arena_polygon(
    frame: np.ndarray,
    arena_points_full: np.ndarray,
):

    points = (
        arena_points_full
        .astype(np.int32)
        .reshape(
            -1,
            1,
            2,
        )
    )

    cv2.polylines(
        frame,
        [points],
        isClosed=True,
        color=(255, 255, 255),
        thickness=2,
    )


# ============================================================
# CALIBRATION FILE FUNCTIONS
# ============================================================

def safe_filename_text(
    text: str,
) -> str:

    return (
        text.strip()
        .replace(" ", "_")
        .replace("/", "_")
        .replace("\\", "_")
        .replace(":", "_")
    )


def save_encoding_calibration(
    animal_id: str,
    video_path: Path,
    selected_radius_1: float,
    selected_radius_2: float,
    common_radius: float,
    radius_difference_percent: float,
) -> Path:

    CALIBRATION_FOLDER.mkdir(
        parents=True,
        exist_ok=True,
    )

    safe_animal_id = (
        safe_filename_text(
            animal_id
        )
    )

    calibration_path = (
        CALIBRATION_FOLDER
        / f"{safe_animal_id}_object_calibration.json"
    )

    calibration_data = {

        "animal_id":
            animal_id,

        "created_from_video":
            video_path.name,

        "encoding_selected_object_1_radius_px":
            float(
                selected_radius_1
            ),

        "encoding_selected_object_2_radius_px":
            float(
                selected_radius_2
            ),

        "encoding_radius_difference_percent":
            float(
                radius_difference_percent
            ),

        "common_object_radius_px":
            float(
                common_radius
            ),

        "investigation_radius_multiplier":
            float(
                INVESTIGATION_RADIUS_MULTIPLIER
            ),

        "investigation_zone_radius_px":
            float(
                common_radius
                * INVESTIGATION_RADIUS_MULTIPLIER
            ),
    }

    with open(
        calibration_path,
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            calibration_data,
            file,
            indent=4,
        )

    return calibration_path


def load_calibration(
    calibration_path: Path,
) -> dict:

    with open(
        calibration_path,
        "r",
        encoding="utf-8",
    ) as file:

        calibration = (
            json.load(
                file
            )
        )

    required_fields = [
        "animal_id",
        "common_object_radius_px",
    ]

    for field in (
        required_fields
    ):

        if (
            field
            not in calibration
        ):

            raise RuntimeError(
                f"Calibration file is missing required field: {field}"
            )

    return calibration


# ============================================================
# BASIC UTILITIES
# ============================================================

def point_to_tuple(
    point: np.ndarray,
) -> tuple[int, int]:

    return (
        int(
            round(
                float(
                    point[0]
                )
            )
        ),

        int(
            round(
                float(
                    point[1]
                )
            )
        ),
    )


def smooth_point(
    current: np.ndarray,
    previous: Optional[np.ndarray],
    alpha: float,
) -> np.ndarray:

    if previous is None:

        return (
            current.copy()
        )

    return (
        alpha
        * current
        +
        (
            1.0
            - alpha
        )
        * previous
    )


def unit_vector(
    vector: np.ndarray,
) -> Optional[np.ndarray]:

    magnitude = float(
        np.linalg.norm(
            vector
        )
    )

    if (
        magnitude
        < 1e-8
    ):

        return None

    return (
        vector
        / magnitude
    )


def format_number(
    value,
    decimals: int = 1,
) -> str:

    if pd.isna(
        value
    ):

        return "NA"

    return (
        f"{float(value):.{decimals}f}"
    )


# ============================================================
# BACKGROUND ESTIMATION
# ============================================================

def estimate_background(
    video_path: Path,
    sample_count: int,
    maximum_time_seconds: float,
) -> np.ndarray:

    video = (
        cv2.VideoCapture(
            str(
                video_path
            )
        )
    )

    if not video.isOpened():

        raise RuntimeError(
            f"Could not open video:\n"
            f"{video_path}"
        )

    fps = float(
        video.get(
            cv2.CAP_PROP_FPS
        )
    )

    total_frames = int(
        video.get(
            cv2.CAP_PROP_FRAME_COUNT
        )
    )

    analysis_frames = min(
        total_frames,
        max(
            1,
            int(
                np.ceil(
                    maximum_time_seconds
                    * fps
                )
            ),
        ),
    )

    actual_sample_count = min(
        sample_count,
        analysis_frames,
    )

    sample_indices = (
        np.linspace(
            0,
            analysis_frames - 1,
            actual_sample_count,
            dtype=int,
        )
    )

    frames = []

    print(
        "Building static background..."
    )

    for number, frame_index in enumerate(
        sample_indices,
        start=1,
    ):

        video.set(
            cv2.CAP_PROP_POS_FRAMES,
            int(
                frame_index
            ),
        )

        success, frame = (
            video.read()
        )

        if success:

            frames.append(
                frame
            )

        print(
            f"\rBackground sample "
            f"{number}/{actual_sample_count}",
            end="",
            flush=True,
        )

    print()

    video.release()

    if (
        len(frames)
        < 10
    ):

        raise RuntimeError(
            "Too few frames were available to estimate background."
        )

    stack = (
        np.stack(
            frames,
            axis=0,
        )
    )

    return (
        np.median(
            stack,
            axis=0,
        )
        .astype(
            np.uint8
        )
    )


# ============================================================
# OBJECT SELECTION
# ============================================================

def select_object_circle(
    frame: np.ndarray,
    object_name: str,
) -> tuple[np.ndarray, float]:

    display = (
        frame.copy()
    )

    cv2.putText(
        display,
        f"Draw tightly around {object_name}, then press ENTER",
        (20, 35),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (0, 255, 255),
        2,
        cv2.LINE_AA,
    )

    window_name = (
        f"Select {object_name}"
    )

    roi = (
        cv2.selectROI(
            window_name,
            display,
            showCrosshair=True,
            fromCenter=False,
        )
    )

    cv2.destroyWindow(
        window_name
    )

    x, y, width, height = (
        map(
            float,
            roi,
        )
    )

    if (
        width <= 0
        or
        height <= 0
    ):

        raise RuntimeError(
            f"No valid selection for {object_name}."
        )

    center = np.array(
        [
            x
            + width / 2.0,

            y
            + height / 2.0,
        ],
        dtype=np.float64,
    )

    radius = (
        width
        + height
    ) / 4.0

    return (
        center,
        radius,
    )


def draw_object_zones(
    frame: np.ndarray,
    object_1_center: np.ndarray,
    object_radius: float,
    object_2_center: np.ndarray,
):

    center_1 = (
        point_to_tuple(
            object_1_center
        )
    )

    center_2 = (
        point_to_tuple(
            object_2_center
        )
    )

    radius = int(
        round(
            object_radius
        )
    )

    zone_radius = int(
        round(
            object_radius
            * INVESTIGATION_RADIUS_MULTIPLIER
        )
    )

    cv2.circle(
        frame,
        center_1,
        radius,
        (0, 255, 255),
        2,
    )

    cv2.circle(
        frame,
        center_1,
        zone_radius,
        (0, 255, 255),
        1,
    )

    cv2.circle(
        frame,
        center_2,
        radius,
        (255, 255, 0),
        2,
    )

    cv2.circle(
        frame,
        center_2,
        zone_radius,
        (255, 255, 0),
        1,
    )

    cv2.putText(
        frame,
        "OBJECT 1",
        (
            center_1[0] - 35,
            center_1[1] - radius - 8,
        ),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.50,
        (0, 255, 255),
        2,
        cv2.LINE_AA,
    )

    cv2.putText(
        frame,
        "OBJECT 2",
        (
            center_2[0] - 35,
            center_2[1] - radius - 8,
        ),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.50,
        (255, 255, 0),
        2,
        cv2.LINE_AA,
    )


# ============================================================
# MOUSE SEGMENTATION WITH POLYGON ARENA MASK
# ============================================================

def detect_mouse_mask_and_contour(
    frame: np.ndarray,
    background: np.ndarray,
    arena_roi,
    arena_mask_roi: np.ndarray,
):

    (
        x,
        y,
        width,
        height,
    ) = (
        arena_roi
    )

    frame_roi = frame[
        y:y + height,
        x:x + width,
    ]

    background_roi = background[
        y:y + height,
        x:x + width,
    ]

    frame_gray = (
        cv2.cvtColor(
            frame_roi,
            cv2.COLOR_BGR2GRAY,
        )
    )

    background_gray = (
        cv2.cvtColor(
            background_roi,
            cv2.COLOR_BGR2GRAY,
        )
    )

    frame_gray = (
        cv2.GaussianBlur(
            frame_gray,
            (5, 5),
            0,
        )
    )

    background_gray = (
        cv2.GaussianBlur(
            background_gray,
            (5, 5),
            0,
        )
    )

    difference = (
        cv2.absdiff(
            frame_gray,
            background_gray,
        )
    )

    _, mask = (
        cv2.threshold(
            difference,
            DIFFERENCE_THRESHOLD,
            255,
            cv2.THRESH_BINARY,
        )
    )

    # Restrict detection to the selected quadrilateral.
    mask = (
        cv2.bitwise_and(
            mask,
            arena_mask_roi,
        )
    )

    open_kernel = (
        cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (
                OPEN_KERNEL_SIZE,
                OPEN_KERNEL_SIZE,
            ),
        )
    )

    mask = (
        cv2.morphologyEx(
            mask,
            cv2.MORPH_OPEN,
            open_kernel,
            iterations=1,
        )
    )

    close_kernel = (
        cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (
                CLOSE_KERNEL_SIZE,
                CLOSE_KERNEL_SIZE,
            ),
        )
    )

    mask = (
        cv2.morphologyEx(
            mask,
            cv2.MORPH_CLOSE,
            close_kernel,
            iterations=CLOSE_ITERATIONS,
        )
    )

    # Reapply polygon after morphology so closing cannot expand
    # outside the actual arena.
    mask = (
        cv2.bitwise_and(
            mask,
            arena_mask_roi,
        )
    )

    contours, _ = (
        cv2.findContours(
            mask,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_NONE,
        )
    )

    valid_contours = [
        contour
        for contour in contours
        if (
            MINIMUM_MOUSE_AREA
            <= cv2.contourArea(
                contour
            )
            <= MAXIMUM_MOUSE_AREA
        )
    ]

    if not valid_contours:

        return (
            None,
            None,
        )

    contour_roi = max(
        valid_contours,
        key=cv2.contourArea,
    )

    mouse_mask_roi = (
        np.zeros_like(
            mask
        )
    )

    cv2.drawContours(
        mouse_mask_roi,
        [
            contour_roi
        ],
        -1,
        255,
        thickness=-1,
    )

    # Ensure final mouse mask also stays in arena.
    mouse_mask_roi = (
        cv2.bitwise_and(
            mouse_mask_roi,
            arena_mask_roi,
        )
    )

    contour_full = (
        contour_roi.copy()
    )

    contour_full[
        :,
        0,
        0
    ] += x

    contour_full[
        :,
        0,
        1
    ] += y

    return (
        mouse_mask_roi,
        contour_full,
    )


def contour_centroid(
    contour: np.ndarray,
):

    moments = (
        cv2.moments(
            contour
        )
    )

    if (
        moments[
            "m00"
        ]
        == 0
    ):

        return None

    return np.array(
        [
            moments["m10"]
            / moments["m00"],

            moments["m01"]
            / moments["m00"],
        ],
        dtype=np.float64,
    )


# ============================================================
# SKELETON GRAPH
# ============================================================

NEIGHBOR_OFFSETS = [
    (-1, -1),
    (-1, 0),
    (-1, 1),
    (0, -1),
    (0, 1),
    (1, -1),
    (1, 0),
    (1, 1),
]


def get_skeleton_neighbors(
    skeleton,
    row,
    col,
):

    neighbors = []

    height, width = (
        skeleton.shape
    )

    for dr, dc in (
        NEIGHBOR_OFFSETS
    ):

        nr = (
            row + dr
        )

        nc = (
            col + dc
        )

        if (
            0 <= nr < height
            and
            0 <= nc < width
            and
            skeleton[
                nr,
                nc
            ]
        ):

            neighbors.append(
                (
                    nr,
                    nc,
                )
            )

    return neighbors


def find_skeleton_endpoints(
    skeleton,
):

    rows, cols = (
        np.where(
            skeleton
        )
    )

    endpoints = []

    for row, col in zip(
        rows,
        cols,
    ):

        if (
            len(
                get_skeleton_neighbors(
                    skeleton,
                    row,
                    col,
                )
            )
            == 1
        ):

            endpoints.append(
                (
                    row,
                    col,
                )
            )

    return endpoints


def trace_endpoint_branch(
    skeleton,
    endpoint,
    max_steps=250,
):

    branch = [
        endpoint
    ]

    previous = None
    current = endpoint

    for _ in range(
        max_steps
    ):

        neighbors = (
            get_skeleton_neighbors(
                skeleton,
                current[0],
                current[1],
            )
        )

        if previous is not None:

            neighbors = [
                neighbor
                for neighbor
                in neighbors
                if neighbor
                != previous
            ]

        if (
            len(
                neighbors
            )
            == 0
        ):
            break

        if (
            len(
                neighbors
            )
            > 1
        ):
            break

        next_pixel = (
            neighbors[0]
        )

        branch.append(
            next_pixel
        )

        previous = current
        current = next_pixel

    return branch


def branch_geometric_length(
    branch,
):

    if (
        len(
            branch
        )
        < 2
    ):

        return 0.0

    length = 0.0

    for index in range(
        1,
        len(branch),
    ):

        row_1, col_1 = (
            branch[
                index - 1
            ]
        )

        row_2, col_2 = (
            branch[
                index
            ]
        )

        length += float(
            np.hypot(
                row_2 - row_1,
                col_2 - col_1,
            )
        )

    return length


def endpoint_to_full_xy(
    endpoint,
    roi_x,
    roi_y,
):

    row, col = (
        endpoint
    )

    return np.array(
        [
            col + roi_x,
            row + roi_y,
        ],
        dtype=np.float64,
    )


# ============================================================
# ENDPOINT THICKNESS
# ============================================================

def endpoint_thickness(
    branch,
    distance_transform,
):

    if not branch:

        return float(
            "nan"
        )

    sample_count = min(
        len(branch),
        ENDPOINT_THICKNESS_SAMPLE_LENGTH,
    )

    values = []

    for row, col in (
        branch[
            :sample_count
        ]
    ):

        values.append(
            float(
                distance_transform[
                    row,
                    col,
                ]
            )
        )

    if not values:

        return float(
            "nan"
        )

    return float(
        np.mean(
            values
        )
    )


# ============================================================
# SKELETON DISTANCE
# ============================================================

def skeleton_distances_from(
    skeleton,
    start,
):

    distances = {
        start:
            0.0
    }

    queue = deque(
        [
            start
        ]
    )

    while queue:

        current = (
            queue.popleft()
        )

        current_distance = (
            distances[
                current
            ]
        )

        for neighbor in (
            get_skeleton_neighbors(
                skeleton,
                current[0],
                current[1],
            )
        ):

            dr = (
                neighbor[0]
                - current[0]
            )

            dc = (
                neighbor[1]
                - current[1]
            )

            step = (
                math.sqrt(
                    2.0
                )
                if (
                    abs(dr)
                    == 1
                    and
                    abs(dc)
                    == 1
                )
                else
                1.0
            )

            new_distance = (
                current_distance
                + step
            )

            if (
                neighbor
                not in distances
                or
                new_distance
                < distances[
                    neighbor
                ]
            ):

                distances[
                    neighbor
                ] = (
                    new_distance
                )

                queue.append(
                    neighbor
                )

    return distances


# ============================================================
# TAIL SELECTION
# ============================================================

def select_tail_candidate(
    endpoints,
    skeleton,
    distance_transform,
    roi_x,
    roi_y,
    previous_tail,
):

    candidates = []

    for endpoint in (
        endpoints
    ):

        branch = (
            trace_endpoint_branch(
                skeleton,
                endpoint,
            )
        )

        branch_length = (
            branch_geometric_length(
                branch
            )
        )

        if (
            branch_length
            < MINIMUM_BRANCH_LENGTH_PIXELS
        ):

            continue

        thickness = (
            endpoint_thickness(
                branch,
                distance_transform,
            )
        )

        if (
            np.isnan(
                thickness
            )
        ):

            continue

        full_point = (
            endpoint_to_full_xy(
                endpoint,
                roi_x,
                roi_y,
            )
        )

        score = (
            thickness
            * TAIL_THICKNESS_WEIGHT
        )

        score -= (
            branch_length
            * TAIL_BRANCH_LENGTH_WEIGHT
        )

        if (
            previous_tail
            is not None
        ):

            temporal_distance = float(
                np.linalg.norm(
                    full_point
                    - previous_tail
                )
            )

            score += (
                temporal_distance
                * TAIL_CONTINUITY_WEIGHT
            )

        candidates.append({

            "endpoint":
                endpoint,

            "point":
                full_point,

            "branch":
                branch,

            "branch_length":
                branch_length,

            "thickness":
                thickness,

            "score":
                float(
                    score
                ),
        })

    if not candidates:

        return (
            None,
            False,
            [],
        )

    candidates.sort(
        key=lambda item:
            item[
                "score"
            ]
    )

    best = (
        candidates[0]
    )

    if (
        len(
            candidates
        )
        == 1
    ):

        confident = True

    else:

        confident = (
            candidates[1][
                "score"
            ]
            -
            best[
                "score"
            ]
            >= TAIL_CONFIDENCE_MARGIN
        )

    return (
        best,
        confident,
        candidates,
    )


# ============================================================
# HEAD SELECTION
# ============================================================

def select_head_candidate(
    endpoints,
    tail_candidate,
    skeleton,
    distance_transform,
    roi_x,
    roi_y,
    previous_head,
):

    tail_endpoint = (
        tail_candidate[
            "endpoint"
        ]
    )

    skeleton_distances = (
        skeleton_distances_from(
            skeleton,
            tail_endpoint,
        )
    )

    candidates = []

    for endpoint in (
        endpoints
    ):

        if (
            endpoint
            == tail_endpoint
        ):

            continue

        branch = (
            trace_endpoint_branch(
                skeleton,
                endpoint,
            )
        )

        branch_length = (
            branch_geometric_length(
                branch
            )
        )

        if (
            branch_length
            < MINIMUM_BRANCH_LENGTH_PIXELS
        ):

            continue

        thickness = (
            endpoint_thickness(
                branch,
                distance_transform,
            )
        )

        if (
            np.isnan(
                thickness
            )
        ):

            continue

        if (
            endpoint
            not in skeleton_distances
        ):

            continue

        tail_distance = float(
            skeleton_distances[
                endpoint
            ]
        )

        full_point = (
            endpoint_to_full_xy(
                endpoint,
                roi_x,
                roi_y,
            )
        )

        score = (
            thickness
            * HEAD_THICKNESS_WEIGHT
        )

        score += (
            tail_distance
            * HEAD_TAIL_DISTANCE_WEIGHT
        )

        score += (
            branch_length
            * HEAD_BRANCH_LENGTH_WEIGHT
        )

        if (
            previous_head
            is not None
        ):

            temporal_distance = float(
                np.linalg.norm(
                    full_point
                    - previous_head
                )
            )

            score -= (
                temporal_distance
                * HEAD_CONTINUITY_WEIGHT
            )

        candidates.append({

            "endpoint":
                endpoint,

            "point":
                full_point,

            "branch":
                branch,

            "branch_length":
                branch_length,

            "thickness":
                thickness,

            "tail_distance":
                tail_distance,

            "score":
                float(
                    score
                ),
        })

    if not candidates:

        return (
            None,
            False,
            [],
        )

    candidates.sort(
        key=lambda item:
            item[
                "score"
            ],
        reverse=True,
    )

    best = (
        candidates[0]
    )

    if (
        len(
            candidates
        )
        == 1
    ):

        confident = True

    else:

        confident = (
            best[
                "score"
            ]
            -
            candidates[1][
                "score"
            ]
            >= HEAD_CONFIDENCE_MARGIN
        )

    return (
        best,
        confident,
        candidates,
    )


# ============================================================
# INVESTIGATION GEOMETRY
# ============================================================

def calculate_angle_degrees(
    vector_1,
    vector_2,
):

    unit_1 = (
        unit_vector(
            vector_1
        )
    )

    unit_2 = (
        unit_vector(
            vector_2
        )
    )

    if (
        unit_1 is None
        or
        unit_2 is None
    ):

        return float(
            "nan"
        )

    cosine = float(
        np.clip(
            np.dot(
                unit_1,
                unit_2,
            ),
            -1.0,
            1.0,
        )
    )

    return (
        math.degrees(
            math.acos(
                cosine
            )
        )
    )


def evaluate_object(
    body_center,
    head,
    object_center,
    object_radius,
):

    center_distance = float(
        np.linalg.norm(
            head
            - object_center
        )
    )

    edge_distance = max(
        0.0,
        center_distance
        - object_radius,
    )

    zone_radius = (
        object_radius
        * INVESTIGATION_RADIUS_MULTIPLIER
    )

    inside_zone = (
        center_distance
        <= zone_radius
    )

    head_direction = (
        head
        - body_center
    )

    object_direction = (
        object_center
        - body_center
    )

    angle = (
        calculate_angle_degrees(
            head_direction,
            object_direction,
        )
    )

    facing_object = (
        not math.isnan(
            angle
        )
        and
        angle
        <= MAXIMUM_HEAD_ANGLE_DEGREES
    )

    investigating = (
        inside_zone
        and
        facing_object
    )

    return {

        "investigating":
            investigating,

        "inside_zone":
            inside_zone,

        "facing_object":
            facing_object,

        "center_distance_px":
            center_distance,

        "edge_distance_px":
            edge_distance,

        "zone_radius_px":
            zone_radius,

        "head_angle_deg":
            angle,
    }


def classify_frame(
    body_center,
    head,
    orientation_confident,
    object_1_center,
    object_radius,
    object_2_center,
):

    result_1 = (
        evaluate_object(
            body_center,
            head,
            object_1_center,
            object_radius,
        )
    )

    result_2 = (
        evaluate_object(
            body_center,
            head,
            object_2_center,
            object_radius,
        )
    )

    if (
        not orientation_confident
        and
        not ALLOW_UNCERTAIN_ORIENTATION_FOR_SCORING
    ):

        state = 0

    else:

        investigating_1 = (
            result_1[
                "investigating"
            ]
        )

        investigating_2 = (
            result_2[
                "investigating"
            ]
        )

        state = 0

        if (
            investigating_1
            and
            not investigating_2
        ):

            state = 1

        elif (
            investigating_2
            and
            not investigating_1
        ):

            state = 2

        elif (
            investigating_1
            and
            investigating_2
        ):

            if (
                result_1[
                    "center_distance_px"
                ]
                <=
                result_2[
                    "center_distance_px"
                ]
            ):

                state = 1

            else:

                state = 2

    return (
        state,
        {

            "object_1_inside_zone":
                result_1[
                    "inside_zone"
                ],

            "object_2_inside_zone":
                result_2[
                    "inside_zone"
                ],

            "object_1_facing":
                result_1[
                    "facing_object"
                ],

            "object_2_facing":
                result_2[
                    "facing_object"
                ],

            "object_1_center_distance_px":
                result_1[
                    "center_distance_px"
                ],

            "object_2_center_distance_px":
                result_2[
                    "center_distance_px"
                ],

            "object_1_edge_distance_px":
                result_1[
                    "edge_distance_px"
                ],

            "object_2_edge_distance_px":
                result_2[
                    "edge_distance_px"
                ],

            "object_1_head_angle_deg":
                result_1[
                    "head_angle_deg"
                ],

            "object_2_head_angle_deg":
                result_2[
                    "head_angle_deg"
                ],
        },
    )


# ============================================================
# BOUT CLEANING
# ============================================================

def bridge_short_gaps(
    states,
    maximum_gap_frames,
):

    cleaned = (
        states.copy()
    )

    index = 0

    while (
        index
        < len(cleaned)
    ):

        if (
            cleaned[
                index
            ]
            != 0
        ):

            index += 1
            continue

        gap_start = (
            index
        )

        while (
            index
            < len(cleaned)
            and
            cleaned[
                index
            ]
            == 0
        ):

            index += 1

        gap_end = (
            index - 1
        )

        gap_length = (
            gap_end
            - gap_start
            + 1
        )

        previous_state = (
            cleaned[
                gap_start - 1
            ]
            if (
                gap_start
                > 0
            )
            else
            0
        )

        next_state = (
            cleaned[
                index
            ]
            if (
                index
                < len(cleaned)
            )
            else
            0
        )

        if (
            gap_length
            <= maximum_gap_frames
            and
            previous_state
            != 0
            and
            previous_state
            == next_state
        ):

            for gap_index in range(
                gap_start,
                gap_end + 1,
            ):

                cleaned[
                    gap_index
                ] = (
                    previous_state
                )

    return cleaned


def remove_short_bouts(
    states,
    minimum_bout_frames,
):

    cleaned = (
        states.copy()
    )

    index = 0

    while (
        index
        < len(cleaned)
    ):

        state = (
            cleaned[
                index
            ]
        )

        if state == 0:

            index += 1
            continue

        bout_start = (
            index
        )

        while (
            index
            < len(cleaned)
            and
            cleaned[
                index
            ]
            == state
        ):

            index += 1

        bout_end = (
            index - 1
        )

        bout_length = (
            bout_end
            - bout_start
            + 1
        )

        if (
            bout_length
            < minimum_bout_frames
        ):

            for bout_index in range(
                bout_start,
                bout_end + 1,
            ):

                cleaned[
                    bout_index
                ] = 0

    return cleaned


def extract_bouts(
    states,
    fps,
):

    bouts = []

    index = 0
    bout_number = 0

    while (
        index
        < len(states)
    ):

        state = (
            states[
                index
            ]
        )

        if state == 0:

            index += 1
            continue

        start_index = (
            index
        )

        while (
            index
            < len(states)
            and
            states[
                index
            ]
            == state
        ):

            index += 1

        end_index = (
            index - 1
        )

        duration_frames = (
            end_index
            - start_index
            + 1
        )

        bout_number += 1

        bouts.append({

            "bout_number":
                bout_number,

            "object":
                f"object_{state}",

            "start_frame":
                start_index + 1,

            "end_frame":
                end_index + 1,

            "start_time_seconds":
                start_index
                / fps,

            "end_time_seconds":
                (
                    end_index
                    + 1
                )
                / fps,

            "duration_seconds":
                duration_frames
                / fps,
        })

    return (
        pd.DataFrame(
            bouts
        )
    )


# ============================================================
# MAIN
# ============================================================

def main():

    OUTPUT_FOLDER.mkdir(
        parents=True,
        exist_ok=True,
    )

    CALIBRATION_FOLDER.mkdir(
        parents=True,
        exist_ok=True,
    )

    # ========================================================
    # USER SETTINGS
    # ========================================================

    video_path = (
        select_video_file()
    )

    duration_minutes = (
        ask_analysis_duration_minutes()
    )

    requested_duration_seconds = (
        duration_minutes
        * 60.0
    )

    session_type = (
        ask_session_type()
    )

    preference_direction = (
        ask_preference_direction()
    )

    time_label = (
        duration_label(
            requested_duration_seconds
        )
    )

    # ========================================================
    # PREFERENCE SETTINGS
    # ========================================================

    if (
        preference_direction
        == 1
    ):

        positive_object = (
            "Object 1"
        )

        preference_formula = (
            "(Object 1 - Object 2) / "
            "(Object 1 + Object 2)"
        )

        preference_filename_label = (
            "obj1positive"
        )

    else:

        positive_object = (
            "Object 2"
        )

        preference_formula = (
            "(Object 2 - Object 1) / "
            "(Object 1 + Object 2)"
        )

        preference_filename_label = (
            "obj2positive"
        )

    # ========================================================
    # ENCODING / TEST CALIBRATION
    # ========================================================

    calibration_path = None

    selected_object_1_radius = (
        np.nan
    )

    selected_object_2_radius = (
        np.nan
    )

    radius_difference_percent = (
        np.nan
    )

    if (
        session_type
        == "encoding"
    ):

        animal_id = (
            ask_animal_id(
                video_path
            )
        )

    else:

        calibration_path = (
            select_calibration_file()
        )

        calibration = (
            load_calibration(
                calibration_path
            )
        )

        animal_id = str(
            calibration[
                "animal_id"
            ]
        )

    # ========================================================
    # OUTPUT FILES
    # ========================================================

    safe_animal_id = (
        safe_filename_text(
            animal_id
        )
    )

    file_prefix = (
        f"{safe_animal_id}_"
        f"{session_type}_"
        f"{time_label}_"
        f"{INVESTIGATION_RADIUS_MULTIPLIER:g}r_"
        f"{preference_filename_label}"
    )

    output_video_path = (
        OUTPUT_FOLDER
        / f"{file_prefix}_"
          f"skeleton_investigation_scoring.mp4"
    )

    frame_csv_path = (
        OUTPUT_FOLDER
        / f"{file_prefix}_"
          f"skeleton_frame_by_frame.csv"
    )

    bout_csv_path = (
        OUTPUT_FOLDER
        / f"{file_prefix}_"
          f"skeleton_investigation_bouts.csv"
    )

    summary_csv_path = (
        OUTPUT_FOLDER
        / f"{file_prefix}_"
          f"skeleton_investigation_summary.csv"
    )

    calibration_image_path = (
        OUTPUT_FOLDER
        / f"{file_prefix}_"
          f"object_zones.png"
    )

    background_image_path = (
        OUTPUT_FOLDER
        / f"{file_prefix}_"
          f"background.png"
    )

    # ========================================================
    # READ VIDEO
    # ========================================================

    video = (
        cv2.VideoCapture(
            str(
                video_path
            )
        )
    )

    if not video.isOpened():

        raise RuntimeError(
            f"Could not open video:\n"
            f"{video_path}"
        )

    fps = float(
        video.get(
            cv2.CAP_PROP_FPS
        )
    )

    frame_count = int(
        video.get(
            cv2.CAP_PROP_FRAME_COUNT
        )
    )

    width = int(
        video.get(
            cv2.CAP_PROP_FRAME_WIDTH
        )
    )

    height = int(
        video.get(
            cv2.CAP_PROP_FRAME_HEIGHT
        )
    )

    success, first_frame = (
        video.read()
    )

    video.release()

    if not success:

        raise RuntimeError(
            "Could not read first frame."
        )

    video_duration_seconds = (
        frame_count
        / fps
    )

    actual_duration_seconds = min(
        requested_duration_seconds,
        video_duration_seconds,
    )

    analysis_frame_count = min(
        frame_count,
        int(
            np.ceil(
                actual_duration_seconds
                * fps
            )
        ),
    )

    print()
    print(
        "========================================"
    )
    print(
        "ANALYSIS SETTINGS"
    )
    print(
        "========================================"
    )

    print(
        f"Animal ID: "
        f"{animal_id}"
    )

    print(
        f"Session: "
        f"{session_type}"
    )

    print(
        f"Video: "
        f"{video_path.name}"
    )

    print(
        f"FPS: "
        f"{fps:.3f}"
    )

    print(
        f"Analysis duration: "
        f"{actual_duration_seconds / 60:.2f} min"
    )

    print(
        f"Preference positive object: "
        f"{positive_object}"
    )

    # ========================================================
    # ARENA POLYGON
    # ========================================================

    print()
    print(
        "Select the four arena corners."
    )

    (
        arena_points_full,
        arena_roi,
        arena_mask_roi,
    ) = (
        select_arena_polygon(
            first_frame
        )
    )

    (
        roi_x,
        roi_y,
        roi_width,
        roi_height,
    ) = (
        arena_roi
    )

    # ========================================================
    # OBJECTS
    # ========================================================

    print()
    print(
        "Select Object 1."
    )

    (
        object_1_center,
        temporary_radius_1,
    ) = (
        select_object_circle(
            first_frame,
            "Object 1",
        )
    )

    print()
    print(
        "Select Object 2."
    )

    (
        object_2_center,
        temporary_radius_2,
    ) = (
        select_object_circle(
            first_frame,
            "Object 2",
        )
    )

    # ========================================================
    # ENCODING CALIBRATION
    # ========================================================

    if (
        session_type
        == "encoding"
    ):

        selected_object_1_radius = (
            temporary_radius_1
        )

        selected_object_2_radius = (
            temporary_radius_2
        )

        common_object_radius = (
            selected_object_1_radius
            + selected_object_2_radius
        ) / 2.0

        radius_difference_percent = (
            abs(
                selected_object_1_radius
                - selected_object_2_radius
            )
            /
            common_object_radius
            * 100.0
        )

        calibration_path = (
            save_encoding_calibration(
                animal_id,
                video_path,
                selected_object_1_radius,
                selected_object_2_radius,
                common_object_radius,
                radius_difference_percent,
            )
        )

        print()
        print(
            "ENCODING CALIBRATION"
        )

        print(
            f"Object 1 radius: "
            f"{selected_object_1_radius:.2f}px"
        )

        print(
            f"Object 2 radius: "
            f"{selected_object_2_radius:.2f}px"
        )

        print(
            f"Common radius: "
            f"{common_object_radius:.2f}px"
        )

        print(
            f"Investigation zone radius: "
            f"{common_object_radius * INVESTIGATION_RADIUS_MULTIPLIER:.2f}px"
        )

        if (
            radius_difference_percent
            >
            OBJECT_RADIUS_QC_WARNING_PERCENT
        ):

            print(
                "WARNING: object radius selections differ "
                f"by {radius_difference_percent:.2f}%."
            )

    # ========================================================
    # TEST CALIBRATION
    # ========================================================

    else:

        calibration = (
            load_calibration(
                calibration_path
            )
        )

        common_object_radius = float(
            calibration[
                "common_object_radius_px"
            ]
        )

        print()
        print(
            "TEST SESSION:"
        )

        print(
            f"Using encoding radius: "
            f"{common_object_radius:.2f}px"
        )

        print(
            f"Using investigation zone radius: "
            f"{common_object_radius * INVESTIGATION_RADIUS_MULTIPLIER:.2f}px"
        )

        print(
            "The test object boxes are used only for center location."
        )

    # ========================================================
    # CALIBRATION IMAGE
    # ========================================================

    calibration_image = (
        first_frame.copy()
    )

    draw_arena_polygon(
        calibration_image,
        arena_points_full,
    )

    draw_object_zones(
        calibration_image,
        object_1_center,
        common_object_radius,
        object_2_center,
    )

    cv2.imwrite(
        str(
            calibration_image_path
        ),
        calibration_image,
    )

    # ========================================================
    # BACKGROUND
    # ========================================================

    background = (
        estimate_background(
            video_path,
            BACKGROUND_SAMPLE_COUNT,
            actual_duration_seconds,
        )
    )

    cv2.imwrite(
        str(
            background_image_path
        ),
        background,
    )

    # ========================================================
    # FIRST PASS: TRACK + SCORE
    # ========================================================

    video = (
        cv2.VideoCapture(
            str(
                video_path
            )
        )
    )

    previous_head = None
    previous_tail = None

    uncertain_streak = 0
    missed_streak = 0

    records = []

    frame_number = 0

    print()
    print(
        "Tracking and scoring..."
    )

    while True:

        success, frame = (
            video.read()
        )

        if not success:
            break

        current_time_seconds = (
            frame_number
            / fps
        )

        if (
            current_time_seconds
            >= actual_duration_seconds
        ):
            break

        frame_number += 1

        (
            mouse_mask_roi,
            contour,
        ) = (
            detect_mouse_mask_and_contour(
                frame,
                background,
                arena_roi,
                arena_mask_roi,
            )
        )

        record = {

            "frame":
                frame_number,

            "time_seconds":
                current_time_seconds,

            "mouse_detected":
                False,

            "orientation_confident":
                False,

            "body_x":
                np.nan,

            "body_y":
                np.nan,

            "head_x":
                np.nan,

            "head_y":
                np.nan,

            "tail_x":
                np.nan,

            "tail_y":
                np.nan,

            "number_of_skeleton_endpoints":
                0,

            "tail_score":
                np.nan,

            "head_score":
                np.nan,

            "object_1_inside_zone":
                False,

            "object_2_inside_zone":
                False,

            "object_1_facing":
                False,

            "object_2_facing":
                False,

            "object_1_center_distance_px":
                np.nan,

            "object_2_center_distance_px":
                np.nan,

            "object_1_edge_distance_px":
                np.nan,

            "object_2_edge_distance_px":
                np.nan,

            "object_1_head_angle_deg":
                np.nan,

            "object_2_head_angle_deg":
                np.nan,

            "object_1_zone_radius_px":
                common_object_radius
                * INVESTIGATION_RADIUS_MULTIPLIER,

            "object_2_zone_radius_px":
                common_object_radius
                * INVESTIGATION_RADIUS_MULTIPLIER,

            "raw_state":
                0,
        }

        if (
            mouse_mask_roi is None
            or
            contour is None
        ):

            missed_streak += 1

            if (
                missed_streak
                >= MAXIMUM_MISSED_FRAMES_BEFORE_RESET
            ):

                previous_head = None
                previous_tail = None
                uncertain_streak = 0

        else:

            missed_streak = 0

            center = (
                contour_centroid(
                    contour
                )
            )

            if center is not None:

                record[
                    "mouse_detected"
                ] = True

                record[
                    "body_x"
                ] = center[0]

                record[
                    "body_y"
                ] = center[1]

                binary_mouse = (
                    mouse_mask_roi
                    > 0
                )

                distance_transform = (
                    distance_transform_edt(
                        binary_mouse
                    )
                )

                skeleton = (
                    skeletonize(
                        binary_mouse
                    )
                )

                endpoints = (
                    find_skeleton_endpoints(
                        skeleton
                    )
                )

                record[
                    "number_of_skeleton_endpoints"
                ] = len(
                    endpoints
                )

                (
                    tail_candidate,
                    tail_confident,
                    _,
                ) = (
                    select_tail_candidate(
                        endpoints,
                        skeleton,
                        distance_transform,
                        roi_x,
                        roi_y,
                        previous_tail,
                    )
                )

                head_candidate = None
                head_confident = False

                if (
                    tail_candidate
                    is not None
                ):

                    (
                        head_candidate,
                        head_confident,
                        _,
                    ) = (
                        select_head_candidate(
                            endpoints,
                            tail_candidate,
                            skeleton,
                            distance_transform,
                            roi_x,
                            roi_y,
                            previous_head,
                        )
                    )

                orientation_confident = (
                    tail_candidate
                    is not None
                    and
                    head_candidate
                    is not None
                    and
                    tail_confident
                    and
                    head_confident
                )

                current_head = None
                current_tail = None

                if (
                    orientation_confident
                ):

                    uncertain_streak = 0

                    current_head = (
                        smooth_point(
                            head_candidate[
                                "point"
                            ],
                            previous_head,
                            SMOOTHING_ALPHA,
                        )
                    )

                    current_tail = (
                        smooth_point(
                            tail_candidate[
                                "point"
                            ],
                            previous_tail,
                            SMOOTHING_ALPHA,
                        )
                    )

                    previous_head = (
                        current_head.copy()
                    )

                    previous_tail = (
                        current_tail.copy()
                    )

                else:

                    uncertain_streak += 1

                    if (
                        previous_head
                        is not None
                        and
                        previous_tail
                        is not None
                        and
                        uncertain_streak
                        <= MAXIMUM_UNCERTAIN_FRAMES_TO_HOLD
                    ):

                        current_head = (
                            previous_head.copy()
                        )

                        current_tail = (
                            previous_tail.copy()
                        )

                if (
                    current_head
                    is not None
                    and
                    current_tail
                    is not None
                ):

                    record[
                        "head_x"
                    ] = (
                        current_head[0]
                    )

                    record[
                        "head_y"
                    ] = (
                        current_head[1]
                    )

                    record[
                        "tail_x"
                    ] = (
                        current_tail[0]
                    )

                    record[
                        "tail_y"
                    ] = (
                        current_tail[1]
                    )

                    record[
                        "orientation_confident"
                    ] = (
                        orientation_confident
                    )

                    if (
                        tail_candidate
                        is not None
                    ):

                        record[
                            "tail_score"
                        ] = (
                            tail_candidate[
                                "score"
                            ]
                        )

                    if (
                        head_candidate
                        is not None
                    ):

                        record[
                            "head_score"
                        ] = (
                            head_candidate[
                                "score"
                            ]
                        )

                    (
                        state,
                        details,
                    ) = (
                        classify_frame(
                            center,
                            current_head,
                            orientation_confident,
                            object_1_center,
                            common_object_radius,
                            object_2_center,
                        )
                    )

                    record.update(
                        details
                    )

                    record[
                        "raw_state"
                    ] = (
                        state
                    )

        records.append(
            record
        )

        if (
            frame_number
            % 100
            == 0
        ):

            print(
                f"\rProcessed "
                f"{frame_number}/"
                f"{analysis_frame_count}",
                end="",
                flush=True,
            )

    print()

    video.release()

    # ========================================================
    # CLEAN STATES
    # ========================================================

    frame_table = (
        pd.DataFrame(
            records
        )
    )

    raw_states = (
        frame_table[
            "raw_state"
        ]
        .astype(int)
        .tolist()
    )

    maximum_gap_frames = max(
        1,
        round(
            MAXIMUM_GAP_SECONDS
            * fps
        ),
    )

    minimum_bout_frames = max(
        1,
        round(
            MINIMUM_BOUT_DURATION_SECONDS
            * fps
        ),
    )

    cleaned_states = (
        bridge_short_gaps(
            raw_states,
            maximum_gap_frames,
        )
    )

    cleaned_states = (
        remove_short_bouts(
            cleaned_states,
            minimum_bout_frames,
        )
    )

    frame_table[
        "cleaned_state"
    ] = (
        cleaned_states
    )

    frame_table[
        "behavior"
    ] = (
        frame_table[
            "cleaned_state"
        ].map(
            {
                0:
                    "not_investigating",

                1:
                    "object_1",

                2:
                    "object_2",
            }
        )
    )

    frame_table, immobility_intervals = flag_frames(
        frame_table, fps, IMMOBILITY_DURATION_SECONDS, IMMOBILITY_TOLERANCE_PX)
    frame_table = exclude_immobility(frame_table, minimum_bout_frames, remove_short_bouts)
    cleaned_states = frame_table['cleaned_state'].astype(int).tolist()
    immobility_intervals.to_csv(
        OUTPUT_FOLDER / f"{file_prefix}_immobility_intervals.csv", index=False)

    frame_table.to_csv(
        frame_csv_path,
        index=False,
    )

    # ========================================================
    # BOUT TABLE
    # ========================================================

    bout_table = (
        extract_bouts(
            cleaned_states,
            fps,
        )
    )

    bout_table = flag_bouts(bout_table, frame_table, fps)
    bout_table.to_csv(
        bout_csv_path,
        index=False,
    )

    # ========================================================
    # SUMMARY
    # ========================================================

    object_1_frames = (
        cleaned_states.count(
            1
        )
    )

    object_2_frames = (
        cleaned_states.count(
            2
        )
    )

    object_1_time = (
        object_1_frames
        / fps
    )

    object_2_time = (
        object_2_frames
        / fps
    )

    total_investigation_time = (
        object_1_time
        + object_2_time
    )

    if (
        bout_table.empty
    ):

        object_1_bouts = 0
        object_2_bouts = 0

    else:

        object_1_bouts = int(
            (
                bout_table[
                    "object"
                ]
                == "object_1"
            ).sum()
        )

        object_2_bouts = int(
            (
                bout_table[
                    "object"
                ]
                == "object_2"
            ).sum()
        )

    if (
        total_investigation_time
        > 0
    ):

        object_1_preference_percent = (
            object_1_time
            / total_investigation_time
            * 100.0
        )

        object_2_preference_percent = (
            object_2_time
            / total_investigation_time
            * 100.0
        )

        if (
            preference_direction
            == 1
        ):

            preference_index = (
                object_1_time
                - object_2_time
            ) / (
                total_investigation_time
            )

        else:

            preference_index = (
                object_2_time
                - object_1_time
            ) / (
                total_investigation_time
            )

    else:

        object_1_preference_percent = (
            np.nan
        )

        object_2_preference_percent = (
            np.nan
        )

        preference_index = (
            np.nan
        )

    detected_frames = int(
        frame_table[
            "mouse_detected"
        ].sum()
    )

    confident_frames = int(
        frame_table[
            "orientation_confident"
        ].sum()
    )

    detection_rate = (
        100.0
        * detected_frames
        / len(frame_table)
        if (
            len(frame_table)
            > 0
        )
        else
        0.0
    )

    confidence_rate = (
        100.0
        * confident_frames
        / detected_frames
        if (
            detected_frames
            > 0
        )
        else
        0.0
    )

    summary_table = pd.DataFrame(
        [
            {

                "animal_id":
                    animal_id,

                "session_type":
                    session_type,

                "video":
                    video_path.name,

                "analysis_duration_seconds":
                    actual_duration_seconds,

                "fps":
                    fps,

                "calibration_file":
                    (
                        calibration_path.name
                        if (
                            calibration_path
                            is not None
                        )
                        else
                        ""
                    ),

                "arena_top_left_x":
                    arena_points_full[0][0],

                "arena_top_left_y":
                    arena_points_full[0][1],

                "arena_top_right_x":
                    arena_points_full[1][0],

                "arena_top_right_y":
                    arena_points_full[1][1],

                "arena_bottom_right_x":
                    arena_points_full[2][0],

                "arena_bottom_right_y":
                    arena_points_full[2][1],

                "arena_bottom_left_x":
                    arena_points_full[3][0],

                "arena_bottom_left_y":
                    arena_points_full[3][1],

                "common_object_radius_px":
                    common_object_radius,

                "investigation_zone_radius_px":
                    (
                        common_object_radius
                        * INVESTIGATION_RADIUS_MULTIPLIER
                    ),

                "investigation_zone_multiplier":
                    INVESTIGATION_RADIUS_MULTIPLIER,

                "encoding_object_1_selected_radius_px":
                    selected_object_1_radius,

                "encoding_object_2_selected_radius_px":
                    selected_object_2_radius,

                "encoding_radius_difference_percent":
                    radius_difference_percent,

                "maximum_head_angle_degrees":
                    MAXIMUM_HEAD_ANGLE_DEGREES,

                "minimum_bout_seconds":
                    MINIMUM_BOUT_DURATION_SECONDS,

                "maximum_gap_seconds":
                    MAXIMUM_GAP_SECONDS,

                "object_1_investigation_seconds":
                    object_1_time,

                "object_2_investigation_seconds":
                    object_2_time,

                "total_investigation_seconds":
                    total_investigation_time,

                "object_1_preference_percent":
                    object_1_preference_percent,

                "object_2_preference_percent":
                    object_2_preference_percent,

                "object_1_bout_count":
                    object_1_bouts,

                "object_2_bout_count":
                    object_2_bouts,

                "preference_positive_object":
                    positive_object,

                "preference_formula":
                    preference_formula,

                "preference_index":
                    preference_index,

                "mouse_detection_rate_percent":
                    detection_rate,

                "orientation_confidence_rate_percent":
                    confidence_rate,
            }
        ]
    )

    summary_table['immobility_duration_threshold_seconds'] = IMMOBILITY_DURATION_SECONDS
    summary_table['immobility_motion_tolerance_px'] = IMMOBILITY_TOLERANCE_PX
    summary_table['immobility_interval_count'] = len(immobility_intervals)
    summary_table['immobility_flagged_seconds'] = frame_table['immobility_flag'].sum() / fps
    summary_table['investigation_immobility_flagged_seconds'] = frame_table['investigation_immobility_flag'].sum() / fps
    summary_table['immobility_action'] = 'exclude_from_investigation' if IMMOBILITY_DURATION_SECONDS > 0 else 'disabled'
    summary_table['immobility_excluded_investigation_seconds'] = frame_table['immobility_excluded'].sum() / fps
    summary_table['post_immobility_short_bout_excluded_seconds'] = frame_table['short_bout_after_immobility_excluded'].sum() / fps
    for object_id in (1, 2):
        before = frame_table['pre_immobility_state'].eq(object_id)
        summary_table[f'object_{object_id}_investigation_before_immobility_seconds'] = before.sum() / fps
        summary_table[f'object_{object_id}_immobility_excluded_seconds'] = (before & frame_table['immobility_excluded']).sum() / fps
    summary_table.to_csv(
        summary_csv_path,
        index=False,
    )

    # ========================================================
    # SECOND PASS: ANNOTATED VIDEO
    # ========================================================

    print()
    print(
        "Creating annotated video..."
    )

    video = (
        cv2.VideoCapture(
            str(
                video_path
            )
        )
    )

    codec = (
        cv2.VideoWriter_fourcc(
            *"mp4v"
        )
    )

    writer = (
        cv2.VideoWriter(
            str(
                output_video_path
            ),
            codec,
            fps,
            (
                width,
                height,
            ),
        )
    )

    if not writer.isOpened():

        video.release()

        raise RuntimeError(
            f"Could not create:\n"
            f"{output_video_path}"
        )

    frame_index = 0

    while True:

        success, frame = (
            video.read()
        )

        if not success:
            break

        if (
            frame_index
            >= len(
                frame_table
            )
        ):
            break

        row = (
            frame_table.iloc[
                frame_index
            ]
        )

        cleaned_state = int(
            row[
                "cleaned_state"
            ]
        )

        draw_arena_polygon(
            frame,
            arena_points_full,
        )

        draw_object_zones(
            frame,
            object_1_center,
            common_object_radius,
            object_2_center,
        )

        if (
            bool(
                row[
                    "mouse_detected"
                ]
            )
            and
            not pd.isna(
                row[
                    "head_x"
                ]
            )
        ):

            center = np.array(
                [
                    row[
                        "body_x"
                    ],
                    row[
                        "body_y"
                    ],
                ],
                dtype=np.float64,
            )

            head = np.array(
                [
                    row[
                        "head_x"
                    ],
                    row[
                        "head_y"
                    ],
                ],
                dtype=np.float64,
            )

            tail = np.array(
                [
                    row[
                        "tail_x"
                    ],
                    row[
                        "tail_y"
                    ],
                ],
                dtype=np.float64,
            )

            center_int = (
                point_to_tuple(
                    center
                )
            )

            head_int = (
                point_to_tuple(
                    head
                )
            )

            tail_int = (
                point_to_tuple(
                    tail
                )
            )

            cv2.circle(
                frame,
                center_int,
                5,
                (0, 0, 255),
                -1,
            )

            cv2.circle(
                frame,
                head_int,
                8,
                (0, 255, 0),
                -1,
            )

            cv2.circle(
                frame,
                tail_int,
                8,
                (255, 0, 0),
                -1,
            )

            cv2.arrowedLine(
                frame,
                center_int,
                head_int,
                (0, 255, 0),
                2,
                tipLength=0.25,
            )

        if (
            cleaned_state
            == 1
        ):

            status = (
                "INVESTIGATING OBJECT 1"
            )

            status_color = (
                0,
                255,
                255,
            )

        elif (
            cleaned_state
            == 2
        ):

            status = (
                "INVESTIGATING OBJECT 2"
            )

            status_color = (
                255,
                255,
                0,
            )

        else:

            status = (
                "NOT INVESTIGATING"
            )

            status_color = (
                255,
                255,
                255,
            )

        if bool(
            row[
                "orientation_confident"
            ]
        ):

            orientation_text = (
                "ORIENTATION: CONFIDENT"
            )

            orientation_color = (
                0,
                255,
                0,
            )

        else:

            orientation_text = (
                "ORIENTATION: UNCERTAIN"
            )

            orientation_color = (
                0,
                165,
                255,
            )

        object_1_distance = (
            format_number(
                row[
                    "object_1_center_distance_px"
                ]
            )
        )

        object_2_distance = (
            format_number(
                row[
                    "object_2_center_distance_px"
                ]
            )
        )

        object_1_angle = (
            format_number(
                row[
                    "object_1_head_angle_deg"
                ]
            )
        )

        object_2_angle = (
            format_number(
                row[
                    "object_2_head_angle_deg"
                ]
            )
        )

        zone_radius_text = (
            f"{common_object_radius * INVESTIGATION_RADIUS_MULTIPLIER:.1f}"
        )

        cv2.putText(
            frame,
            (
                f"{animal_id} | "
                f"{session_type.upper()} | "
                f"positive={positive_object}"
            ),
            (
                20,
                35,
            ),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.50,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )

        cv2.putText(
            frame,
            orientation_text,
            (
                20,
                60,
            ),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.50,
            orientation_color,
            2,
            cv2.LINE_AA,
        )

        cv2.putText(
            frame,
            (
                f"Obj1 distance "
                f"{object_1_distance}/"
                f"{zone_radius_text}px | "
                f"angle "
                f"{object_1_angle} deg"
            ),
            (
                20,
                85,
            ),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 255, 255),
            2,
            cv2.LINE_AA,
        )

        cv2.putText(
            frame,
            (
                f"Obj2 distance "
                f"{object_2_distance}/"
                f"{zone_radius_text}px | "
                f"angle "
                f"{object_2_angle} deg"
            ),
            (
                20,
                108,
            ),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (255, 255, 0),
            2,
            cv2.LINE_AA,
        )

        cv2.putText(
            frame,
            status,
            (
                20,
                height - 50,
            ),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.70,
            status_color,
            2,
            cv2.LINE_AA,
        )

        cv2.putText(
            frame,
            (
                f"Time: "
                f"{row['time_seconds']:.2f}s | "
                f"arena | zone {INVESTIGATION_RADIUS_MULTIPLIER:g}r"
            ),
            (
                20,
                height - 20,
            ),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.50,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )

        if bool(row['immobility_flag']):
            label = 'EXCLUDED: IMMOBILITY' if bool(row['immobility_excluded']) else 'IMMOBILITY'
            cv2.putText(frame, label, (20, height - 80), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, (0, 140, 255), 2, cv2.LINE_AA)
        writer.write(
            frame
        )

        frame_index += 1

        if (
            frame_index
            % 100
            == 0
        ):

            print(
                f"\rAnnotated "
                f"{frame_index}/"
                f"{len(frame_table)}",
                end="",
                flush=True,
            )

    print()

    video.release()
    writer.release()
    cv2.destroyAllWindows()

    # ========================================================
    # FINAL REPORT
    # ========================================================

    print()
    print(
        "========================================"
    )

    print(
        "ANALYSIS COMPLETE"
    )

    print(
        "========================================"
    )

    print(
        f"Animal: "
        f"{animal_id}"
    )

    print(
        f"Session: "
        f"{session_type}"
    )

    print(
        f"Object 1 investigation: "
        f"{object_1_time:.2f} s"
    )

    print(
        f"Object 2 investigation: "
        f"{object_2_time:.2f} s"
    )

    print(
        f"Total investigation: "
        f"{total_investigation_time:.2f} s"
    )

    print(
        f"Preference positive object: "
        f"{positive_object}"
    )

    print(
        f"Preference formula: "
        f"{preference_formula}"
    )

    if not np.isnan(
        preference_index
    ):

        print(
            f"Preference index: "
            f"{preference_index:.3f}"
        )

    else:

        print(
            "Preference index: NA"
        )

    print(
        f"Mouse detection rate: "
        f"{detection_rate:.2f}%"
    )

    print(
        f"Orientation confidence rate: "
        f"{confidence_rate:.2f}%"
    )

    print()
    print(
        "Annotated video:"
    )

    print(
        output_video_path
    )

    print()
    print(
        "Frame-by-frame CSV:"
    )

    print(
        frame_csv_path
    )

    print()
    print(
        "Bout CSV:"
    )

    print(
        bout_csv_path
    )

    print()
    print(
        "Summary CSV:"
    )

    print(
        summary_csv_path
    )

    print()
    print(
        "Object-zone image:"
    )

    print(
        calibration_image_path
    )


if __name__ == "__main__":
    main()
