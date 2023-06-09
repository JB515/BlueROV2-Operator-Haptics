'''Experiment suite for ROV cross-current experiment'''

from tokenize import Double
import PySimpleGUI as sg
import cv2
import gi
import numpy as np
import sys
from utils import ARUCO_DICT
import argparse
import time
from pymavlink import mavutil
import copy
import threading
import socket
import pickle
import json
from math import sqrt
from scipy.spatial.transform import Rotation as R
import csv

gi.require_version('Gst', '1.0')
from gi.repository import Gst


class Video():
    """BlueRov video capture class constructor

    Attributes:
        port (int): Video UDP port
        video_codec (string): Source h264 parser
        video_decode (string): Transform YUV (12bits) to BGR (24bits)
        video_pipe (object): GStreamer top-level pipeline
        video_sink (object): Gstreamer sink element
        video_sink_conf (string): Sink configuration
        video_source (string): Udp source ip and port
        latest_frame (np.ndarray): Latest retrieved video frame
    """

    def __init__(self, port=5600):
        """Summary

        Args:
            port (int, optional): UDP port
        """

        Gst.init(None)

        self.port = port
        self.latest_frame = self._new_frame = None

        # [Software component diagram](https://www.ardusub.com/software/components.html)
        # UDP video stream (:5600)
        self.video_source = 'udpsrc port={}'.format(self.port)
        # [Rasp raw image](http://picamera.readthedocs.io/en/release-0.7/recipes2.html#raw-image-capture-yuv-format)
        # Cam -> CSI-2 -> H264 Raw (YUV 4-4-4 (12bits) I420)
        self.video_codec = '! application/x-rtp, payload=96 ! rtph264depay ! h264parse ! avdec_h264'
        # Python don't have nibble, convert YUV nibbles (4-4-4) to OpenCV standard BGR bytes (8-8-8)
        self.video_decode = \
            '! decodebin ! videoconvert ! video/x-raw,format=(string)BGR ! videoconvert'
        # Create a sink to get data
        self.video_sink_conf = \
            '! appsink emit-signals=true sync=false max-buffers=2 drop=true'

        self.video_pipe = None
        self.video_sink = None

        self.run()

    def start_gst(self, config=None):
        """ Start gstreamer pipeline and sink
        Pipeline description list e.g:
            [
                'videotestsrc ! decodebin', \
                '! videoconvert ! video/x-raw,format=(string)BGR ! videoconvert',
                '! appsink'
            ]

        Args:
            config (list, optional): Gstreamer pileline description list
        """

        if not config:
            config = \
                [
                    'videotestsrc ! decodebin',
                    '! videoconvert ! video/x-raw,format=(string)BGR ! videoconvert',
                    '! appsink'
                ]

        command = ' '.join(config)
        self.video_pipe = Gst.parse_launch(command)
        self.video_pipe.set_state(Gst.State.PLAYING)
        self.video_sink = self.video_pipe.get_by_name('appsink0')

    @staticmethod
    def gst_to_opencv(sample):
        """Transform byte array into np array

        Args:
            sample (TYPE): Description

        Returns:
            TYPE: Description
        """
        buf = sample.get_buffer()
        caps_structure = sample.get_caps().get_structure(0)
        array = np.ndarray(
            (
                caps_structure.get_value('height'),
                caps_structure.get_value('width'),
                3
            ),
            buffer=buf.extract_dup(0, buf.get_size()), dtype=np.uint8)
        return array

    def frame(self):
        """ Get Frame

        Returns:
            np.ndarray: latest retrieved image frame
        """
        if self.frame_available:
            self.latest_frame = self._new_frame
            # reset to indicate latest frame has been 'consumed'
            self._new_frame = None
        return self.latest_frame

    def frame_available(self):
        """Check if a new frame is available

        Returns:
            bool: true if a new frame is available
        """
        return self._new_frame is not None

    def run(self):
        """ Get frame to update _new_frame
        """

        self.start_gst(
            [
                self.video_source,
                self.video_codec,
                self.video_decode,
                self.video_sink_conf
            ])

        self.video_sink.connect('new-sample', self.callback)

    def callback(self, sink):
        sample = sink.emit('pull-sample')
        self._new_frame = self.gst_to_opencv(sample)

        return Gst.FlowReturn.OK

def maprange( a, b, s):
	(a1, a2), (b1, b2) = a, b
	return  b1 + ((s - a1) * (b2 - b1) / (a2 - a1))

def pose_esitmation(frame, aruco_dict_type, matrix_coefficients, distortion_coefficients, tagSize):

    '''
    frame - Frame from the video stream
    aruco_dict_type - self-explanatory, the aruco dictionary containing the tag
    matrix_coefficients - Intrinsic matrix of the calibrated camera
    distortion_coefficients - Distortion coefficients associated with your camera
    tagSize - the size of the tag (black area) in m = 1.2

    return:-
    frame - The frame with the axis drawn on it
    rvec - rotation vector (opencv condensed version)
    tvec - translation vector, translation in x,y,z in m
    '''

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    cv2.aruco_dict = cv2.aruco.Dictionary_get(aruco_dict_type)
    parameters = cv2.aruco.DetectorParameters_create()


    corners, ids, rejected_img_points = cv2.aruco.detectMarkers(gray, cv2.aruco_dict,parameters=parameters,
        cameraMatrix=matrix_coefficients,
        distCoeff=distortion_coefficients)

        # If markers are detected
    if len(corners) > 0:
        for i in range(0, len(ids)):
            # Estimate pose of each marker and return the values rvec and tvec---(different from those of camera coefficients)
            rvec, tvec, markerPoints = cv2.aruco.estimatePoseSingleMarkers(corners[i], tagSize, matrix_coefficients,
                                                                       distortion_coefficients)
            #rmat = cv2.Rodrigues(rvec)[0]
            #print(rmat)


            # Draw a square around the markers
            cv2.aruco.drawDetectedMarkers(frame, corners)

            # Draw Axis
            cv2.aruco.drawAxis(frame, matrix_coefficients, distortion_coefficients, rvec, tvec, 0.1)
            #cv2.putText(frame,f't:{-1*tvec}, r:{rvec}', (25, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 0, 255), 2, 3)

    else:
        tvec = [[[0,0,0]]]
        rvec = [[[0,0,0]]]
    return frame,tvec[0][0],rvec[0][0]

class repeatedTimer(object):
    def __init__(self, interval, function, *args, **kwargs):
        self._timer     = None
        self.interval   = interval
        self.function   = function
        self.args       = args
        self.kwargs     = kwargs
        self.is_running = False
        self.start()

    def _run(self):
        self.is_running = False
        self.start()
        self.function(*self.args, **self.kwargs)

    def start(self):
        if not self.is_running:
            self._timer = threading.Timer(self.interval, self._run)
            self._timer.start()
            self.is_running = True

    def stop(self):
        self._timer.cancel()
        self.is_running = False

'''LIGHTING FUNCTIONS'''

def set_servo_pwm(master, servo_n, microseconds):
    """ Sets AUX 'servo_n' output PWM pulse-width.

    Uses https://mavlink.io/en/messages/common.html#MAV_CMD_DO_SET_SERVO

    'servo_n' is the AUX port to set (assumes port is configured as a servo).
        Valid values are 1-3 in a normal BlueROV2 setup, but can go up to 8
        depending on Pixhawk type and firmware.
    'microseconds' is the PWM pulse-width to set the output to. Commonly
        between 1100 and 1900 microseconds.

    """
    # master.set_servo(servo_n+8, microseconds) or:
    master.mav.command_long_send(
        master.target_system, master.target_component,
        mavutil.mavlink.MAV_CMD_DO_SET_SERVO,
        0,            # first transmission of this command
        servo_n + 8,  # servo instance, offset by 8 MAIN outputs
        microseconds, # PWM pulse-width
        0,0,0,0,0     # unused parameters
    )

def lightOn(master):
    set_servo_pwm(master, 1, 1500)
    print("light on")
    return

def lightOff(master):
    set_servo_pwm(master, 1, 1100)
    print("light off")
    return

def flashLights(master, n):
    for i in range(0,n,1):
        lightOn(master)
        time.sleep(0.25)
        lightOff(master)
        time.sleep(1)
    return

def lightSignal(master, fast, slow):
    print("signalling with lights")
    if fast > 0:
        print("fast cycle")
        for i in range(0,fast,1):
            lightOn(master)
            time.sleep(0.25)
            lightOff(master)
            time.sleep(0.5)

    if slow > 0:
        print("slow cycle")
        for i in range(0,slow,1):
            lightOn(master)
            time.sleep(0.75)
            lightOff(master)
            time.sleep(0.5)
    return

'''ROBOT HELPERS'''

def set_rc_channel_pwm(channel_id, pwm=1500):
    """ Set RC channel pwm value
    Args:
        channel_id (TYPE): Channel ID
        pwm (int, optional): Channel pwm value 1100-1900
    """
    if channel_id < 1 or channel_id > 18:
        print("Channel does not exist.")
        return

    # Mavlink 2 supports up to 18 channels:
    # https://mavlink.io/en/messages/common.html#RC_CHANNELS_OVERRIDE
    rc_channel_values = [65535 for _ in range(18)]
    rc_channel_values[channel_id - 1] = pwm
    master.mav.rc_channels_override_send(
        master.target_system,                # target_system
        master.target_component,             # target_component
        *rc_channel_values)                  # RC channel list, in microseconds.

    '''
    RC Inputs:
    1 -> Pitch
    2 -> Roll
    3 -> Throttle
    4 -> Yaw
    5 -> Forward
    6 -> Lateral (strafe?)
    1100 = full dir1, 1900 = full dir2, 1500 = stop
    '''

def clearMotion():
    # Mavlink 2 supports up to 18 channels:
    # https://mavlink.io/en/messages/common.html#RC_CHANNELS_OVERRIDE
    rc_channel_values = [65535 for _ in range(18)]
    rc_channel_values[0] = 1500
    rc_channel_values[1] = 1500
    rc_channel_values[2] = 1500
    rc_channel_values[3] = 1500
    rc_channel_values[4] = 1500
    rc_channel_values[5] = 1500
    master.mav.rc_channels_override_send(
        master.target_system,                # target_system
        master.target_component,             # target_component
        *rc_channel_values)                  # RC channel list, in microseconds.

    '''
    RC Inputs:
    1 -> Pitch
    2 -> Roll
    3 -> Throttle
    4 -> Yaw
    5 -> Forward
    6 -> Lateral (strafe?)
    1100 = full dir1, 1900 = full dir2, 1500 = stop
    '''

def heartbeat_helper(master):
    print('start heartbeat thread')
    while True:
        master.mav.heartbeat_send(
            6, #MAVTYPE = MAV_TYPE_GCS
            8, #MAVAUTOPILOT = MAV_AUTOPILOT_INVALID
            128, # MAV_MODE = MAV_MODE_FLAG_SAFETY_ARMED, have also tried 0 here
            0,0)
        #print('sent heartbeat')
        time.sleep(0.9)

def request_message_interval(message_id: int, frequency_hz: float):
    """
    Request MAVLink message in a desired frequency,
    documentation for SET_MESSAGE_INTERVAL:
        https://mavlink.io/en/messages/common.html#MAV_CMD_SET_MESSAGE_INTERVAL

    Args:
        message_id (int): MAVLink message ID
        frequency_hz (float): Desired frequency in Hz
    """
    master.mav.command_long_send(
        master.target_system, master.target_component,
        mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL, 0,
        message_id, # The MAVLink message ID
        1e6 / frequency_hz, # The interval between two messages in microseconds. Set to -1 to disable and 0 to request default rate.
        0, 0, 0, 0, # Unused parameters
        0, # Target address of message stream (if message has target address fields). 0: Flight-stack default (recommended), 1: address of requestor, 2: broadcast.
    )

'''GUI DEFINITIONS'''

def LEDIndicator(key, radius):
    return sg.Graph(canvas_size=(radius, radius),
             graph_bottom_left=(-radius, -radius),
             graph_top_right=(radius, radius),
             pad=(0, 0), key=key)

def SetLED(window, key, color):
    graph = window[key]
    graph.erase()
    graph.draw_circle((0, 0), 12, fill_color=color, line_color=color)

def hapticViz(key):
    width=300
    canvas = sg.Graph(canvas_size=(width,width/2), graph_bottom_left=(0,0), graph_top_right=(1200,600), background_color='white', border_width=2, key=key)
    return canvas

def hapticVizLine(window, key):
    canvas = window[key]
    canvas.draw_line(point_from=(88,300),point_to=(1112,300),color='green',width=2)

def hapticVizUpdate(window, key, circleID, force, position):
    canvas = window[key]
    if circleID is not None:
        canvas.delete_figure(circleID)
    newCircle = canvas.draw_circle(center_location=(maprange((0,2000),(0,1200),position),300),radius=(maprange((0,500),(5,50),force)),fill_color='red', line_width=0)
    return newCircle


'''Logging threads'''

def hapticsThread(haptics):
    global hapticsIn
    global hapticsOut

    while True:
        #Send haptics output
        hapticBytesOut = pickle.dumps(hapticsOut)
        haptics.send(hapticBytesOut)

        #get haptics input
        hapticBytesIn = haptics.recv(128)
        hapticDataIn = pickle.loads(hapticBytesIn)
        #print(time.perf_counter())
        #print(hapticDataIn)
        hapticsIn[0] = hapticDataIn[0]
        hapticsIn[1] = hapticDataIn[1]

''' MAIN '''

print("Entered main")

sg.theme('Dark Blue 15')

#Connect to blueROV2 over MAVlink
master = mavutil.mavlink_connection('udpin:0.0.0.0:14550')
master.wait_heartbeat()
print("Connected to robot!")

#Start heartbeat thread
heartbeatThread = threading.Thread(target=heartbeat_helper, args=(master,))
heartbeatThread.daemon = True
heartbeatThread.start()

#Set robot mode

# Choose a mode
mode = 'ALT_HOLD'     #DEPTH_HOLD

# Check if mode is available
if mode not in master.mode_mapping():
    print('Unknown mode : {}'.format(mode))
    print('Try:', list(master.mode_mapping().keys()))
    sys.exit(1)

# Get mode ID
mode_id = master.mode_mapping()[mode]
print(f'I know that mode: {mode_id}')
# Set new mode
# master.mav.command_long_send(
#    master.target_system, master.target_component,
#    mavutil.mavlink.MAV_CMD_DO_SET_MODE, 0,
#    0, mode_id, 0, 0, 0, 0, 0) or:

master.set_mode(mode_id)
##master.mav.set_mode_send(
#    master.target_system,
#    mavutil.mavlink.MAV_CMD_DO_SET_MODE,
#    mode_id)


print('Sent mode message')
while True:
    # Wait for ACK command
    # Would be good to add mechanism to avoid endlessly blocking
    # if the autopilot sends a NACK or never receives the message
    ack_msg = master.recv_match(type='COMMAND_ACK', blocking=True)
    ack_msg = ack_msg.to_dict()

    # Continue waiting if the acknowledged command is not `set_mode`
    if ack_msg['command'] != mavutil.mavlink.MAV_CMD_DO_SET_MODE:
        continue

    # Print the ACK result !
    print(mavutil.mavlink.enums['MAV_RESULT'][ack_msg['result']].description)
    break

master.wait_heartbeat()

#request_message_interval(mavutil.mavlink.MAVLINK_MSG_ID_AHRS3, 0.03)

#Create video object bound to ROV webcam
video = Video()

#Connect to haptic device over TCP
host = '10.55.0.1'  # as both code is running on same pc
port = 8787  # socket server port number

haptics = socket.socket()  # instantiate
haptics.connect((host, port))  # connect to the server
print("Connected to haptics!")

hapticsIn = [0,0]
hapticsOut = [0,500]

print('Initialising stream...')
waited = 0
i = 0

#initialize experiment setup data
participant = 0
repeat = 1
conditionString = 'No current'
tagSize = 1.12
touchControlEnabled = False

#Layout experiment parameter UI elements
setupRow = [
    [sg.Text('Participant ID: '),
    sg.Input(key="-PID-", size=(4,1), default_text=participant),
    sg.Text(" "),
    sg.VSeperator(),
    sg.Text("  Conditions:"),
    sg.Radio("Haptics", "Conditions", default=False, key="-condH-"),
    sg.Radio("Current, no haptics", "Conditions", default=False, key="-condC-"),
    sg.Radio("Control", "Conditions", default=False, key="-condN-"),
    sg.Radio("Training", "Conditions", default=False, key="-condT-"),
    sg.VSeperator(),
    sg.Text(" Tag size (m): "),
    sg.Input(key='-tag-', size=(4,1),default_text=1.12),
    sg.Text(" "),
    sg.VSeperator(),
    sg.Text(" Repeat: "),
    sg.Input(key='-rep-', size=(4,1),default_text=repeat),
    sg.Text(" "),
    sg.Button('Start'),
    sg.Button('Pass', disabled=True),
    sg.Button('Fail', disabled=True)]
]

statusCol1 = [
    [sg.Text("Leak:    "), LEDIndicator("-LEAK-",40)],
    [sg.Text("Armed:  "), LEDIndicator("-ARM-",40)],
    [sg.Text("Logging:"), LEDIndicator("-LOG-",40)]
]
statusCol2 = [
    [sg.Text("Vibration:    "), LEDIndicator("-VIBE-",40)],
    [sg.Text("Hard:  "), LEDIndicator("-HARD-",40)],
    [sg.Text("Control:  "), LEDIndicator("-TOUCH-",40)]
]
controlCol1 = [
    [sg.Button("Arm")],
    [sg.Button("Confirm", disabled=True)],
    [sg.Button("Disarm", disabled=True)],
    [sg.Button("Touchpad", disabled=True)]
]
controlCol2 = [
    [sg.Button("Up", disabled=True)],
    [sg.Button("Left", disabled=True)],
    [sg.Button("StrafeL", disabled=True)],
    [sg.Button("Manual", disabled=False)]
    
]
controlCol3 = [
    [sg.Button("Forward", disabled=True)],
    [sg.Button("All Stop", disabled=True)],
    [sg.Button("Reverse", disabled=True)],
    [sg.Button("Straight", disabled=True)]
]
controlCol4 = [
    [sg.Button("Down", disabled=True)],
    [sg.Button("Right", disabled=True)],
    [sg.Button("StrafeR", disabled=True)],
    [sg.Button("Stabilize", disabled=True)]
]

hapticsCol1 = [
    [sg.Button("Start vibration")],
    [sg.Button("Stop vibration")]
]
hapticsCol2 = [
    [sg.Button("Go soft")],
    [sg.Button("Go hard")]
]
hapticsCol3 = [
    [sg.Button("Zero")],
    [sg.Button("Print")]
]
poseCol1 = [
    [sg.Text("x: "), sg.Text("0", key="-X-"), sg.Text("m")],
    [sg.Text("y: "), sg.Text("0", key="-Y-"), sg.Text("m")],
    [sg.Text("z: "), sg.Text("0", key="-Z-"), sg.Text("m")]
]
poseCol2 = [
    [sg.Text("   Spd:  "), sg.Text("0", key="-SPD-"), sg.Text("m/s")],
    [sg.Text("   Yaw:  "), sg.Text("0", key="-YAW-"), sg.Text("deg")],
    [sg.Text("   Tgt:  "), sg.Text("0", key="-TGT-"), sg.Text("m")]
]

poseCol3 = [
    [sg.Text("   Depth:  "), sg.Text("0", key="-DEPTH-"), sg.Text("m")],
    [sg.Text("    Time:  "), sg.Text("0", key="-TIME-"), sg.Text("s")]
]

illuminatorCol1 = [
    [sg.Button("Ready to start", )],
    [sg.Button("Ready to end")]

]

illuminatorCol2 = [
    [sg.Button("Move area")],
    [sg.Button("EMERGENCY", button_color="RED")]
    
]

#Layout ROV command elements
lightSignalsColumn = [
    [sg.Text("STATUS", size=(40,1))],
    [sg.Column(statusCol1), sg.Column(statusCol2)],
    [sg.HorizontalSeparator()],
    [sg.Text("POSE", size=(40,1))],
    [sg.Column(poseCol1), sg.Column(poseCol2), sg.Column(poseCol3)],
    [sg.HorizontalSeparator()],
    [sg.Text("ROBOT CONTROLS", size=(40,1))],
    [sg.Column(controlCol1), sg.Column(controlCol2), sg.Column(controlCol3), sg.Column(controlCol4)],
    [sg.HorizontalSeparator()],
    [sg.Text("ILLUMINATOR SIGNALS", size=(40,1))],
    [sg.Column(illuminatorCol1), sg.Column(illuminatorCol2)],
    [sg.HorizontalSeparator()],
    [sg.Text("HAPTICS CONTROLS", size=(40,1))],
    [sg.Column(hapticsCol1), sg.Column(hapticsCol2), sg.Column(hapticsCol3)],
    [hapticViz('touchpad')],
    [sg.HorizontalSeparator()],
    [sg.Text("CAMERA CONTROLS", size=(40,1))],
    [sg.Button("Raw still"), sg.Button("Circle still"), sg.Button("CV still")]
]

cmdColumn = [
    [sg.Frame("Setup", setupRow), sg.Frame("Battery", [[sg.Text("99%", key='-BATT-')]],key='battframe')],
    [sg.Graph((1280,720),(0,720), (1280,0), key='tagView')]
]

#User view is just the robot camera
userLayout = [
    [sg.Graph((1280,720),(0,720), (1280,0), key='robotView')]
    ]

#Command and control window layout
commandLayout = [
    [sg.Column(cmdColumn, vertical_alignment='t'),
    sg.VerticalSeparator(),
    sg.Frame("Controls", lightSignalsColumn, vertical_alignment='top')]
    ]
    
#Set UI windows
userWindow = sg.Window('ROV Teleoperation - User View', userLayout, no_titlebar=True, background_color='black', element_padding=0, location=(425,100))  #(270,10)
commandWindow = sg.Window('ROV Teleoperation - Command and Control', commandLayout)

userWindow.Finalize()
commandWindow.Finalize()

hapticVizLine(commandWindow, 'touchpad')
vizCircle = hapticVizUpdate(commandWindow, 'touchpad', None, 0, 512)

robotViewElem = userWindow['robotView']                     # type: sg.Graph
tagViewElem = commandWindow['tagView']                      # type: sg.Graph

SetLED(commandWindow,"-LEAK-","#460065")             #use red for on
SetLED(commandWindow,"-ARM-","#460065")              #use red for on
SetLED(commandWindow,"-LOG-","#004665")                  #Use green1 for on
SetLED(commandWindow,"-VIBE-","#004665")                  #Use green1 for on
SetLED(commandWindow,"-HARD-","#004665")                  #Use green1 for on
SetLED(commandWindow,"-TOUCH-","#004665")                  #Use green1 for on

#Some variables that will be filled later
rawVideoLog = None
markupVideoLog = None
robot_img = None
tag_img = None

#Parameters for ArUco localisation
aruco_dict_type = ARUCO_DICT['DICT_4X4_100']
k = np.load("calibration_matrix.npy")
d = np.load("distortion_coefficients.npy")

#Logging flags default to false
saveVideo = False
saveData = False

posZero = 1000
fingerPos = 1000
adjustedFingerPos = fingerPos
fingerForce = 512
adjustedFingerForce = fingerForce

networkRead = threading.Thread(target=hapticsThread, args=(haptics,))
networkRead.daemon = True
networkRead.start()

averages = 15
avgx = 0
avgy = 0
avgz = 0

avgroll = 0
avgpitch = 0
avgyaw = 0

speed = 0
turn = 0

expTime = 0
depth = 0
gndspd = 0

startTime = 0
startTimePC = 0
startYaw = 0
runFail = False
failReason = 'Unspecified'

startFrame = True
# ---===--- MAIN LOOP Read, process and display frames, operate the GUI, send haptics data --- #
while True:
    #print(time.perf_counter())
    if video.frame_available():
        # Only retrieve and display a frame if it's new
        frame = video.frame()
        circleFrame = frame.copy()
        cv2.circle(circleFrame,(640,360),80,(0,0,255),5)
        tagFrame = frame.copy()
        if saveVideo:
            rawVideoLog.write(frame)
            #pass
        startFrame = False
    elif startFrame:
        frame = cv2.imread("tagSamples/ROVCam_8.jpg")
        circleFrame = tagFrame.copy()
        cv2.circle(circleFrame,(640,360),80,(0,0,255),5)
        tagFrame = frame.copy()

    tagFrame,tvec,rvec = pose_esitmation(tagFrame, aruco_dict_type, k, d, tagSize)

    
    cv2.circle(tagFrame,(640,360),100,(0,0,255),10)

    robotViewBytes=cv2.imencode('.ppm', circleFrame)[1].tobytes()       # on some ports, will need to change to png
    if robot_img:
        robotViewElem.delete_figure(robot_img)             # delete previous image
    robot_img = robotViewElem.draw_image(data=robotViewBytes, location=(0,0))    # draw new image
    
    newmsg = master.recv_match(type=('SCALED_IMU2'), blocking=False)
    #newmsg = master.messages['SCALED_IMU2']
    if newmsg is not None:
        msg = newmsg.to_dict()
    
    newcompassmsg = master.messages['VFR_HUD']
    if newcompassmsg is not None:
        compassmsg = newcompassmsg.to_dict()
        
    newstatusmsg = master.messages['SYS_STATUS']
    if newstatusmsg is not None:
        statusmsg = newstatusmsg.to_dict()
    

    batteryLife = statusmsg['battery_remaining']

    if batteryLife < 20:
        commandWindow['-BATT-'].ParentRowFrame.config(background='red')

    avgx -= avgx/averages
    avgx += tvec[0]/averages

    avgy -= avgy/averages
    avgy += tvec[1]/averages

    avgz -= avgz/averages
    avgz += tvec[2]/averages

    avgroll -= avgroll/averages
    avgroll += msg["ygyro"]/averages

    avgpitch -= avgpitch/averages
    avgpitch += msg["xgyro"]/averages

    '''
    avgyaw -= avgyaw/averages
    avgyaw += msg["zgyro"]/averages
    '''

    avgyaw = compassmsg['heading']
    gndspd = compassmsg['groundspeed']
    depth = compassmsg['alt']

    #print(tvec)
    targetDist = sqrt((pow(avgx,2) + pow((avgz-2),2)))

    if conditionString == 'Haptics' and saveData:
        if (avgz < 4.1) and (avgz > 3.7):
            #hapticsOut[0] = 1
            SetLED(commandWindow,"-VIBE-","green1")
        else:
            #hapticsOut[0] = 0
            SetLED(commandWindow,"-VIBE-","#004665")
        if targetDist < 0.5:
            #hapticsOut[1] = 500 * (targetDist-0.5)/(-0.5)
            SetLED(commandWindow,"-HARD-","green1")
        else:
            #hapticsOut[1] = 0
            SetLED(commandWindow,"-HARD-","#004665")
    if saveVideo:
        markupVideoLog.write(tagFrame)
        #print(time.perf_counter())
    tagViewBytes=cv2.imencode('.ppm', tagFrame)[1].tobytes()       # on some ports, will need to change to png
    if tag_img:
        tagViewElem.delete_figure(tag_img)             # delete previous image
    tag_img = tagViewElem.draw_image(data=tagViewBytes, location=(0,0))    # draw new image

    fingerPos = hapticsIn[0]
    fingerForce = hapticsIn[1]

    vizCircle = hapticVizUpdate(commandWindow, 'touchpad', vizCircle, fingerForce, fingerPos)

    if touchControlEnabled:
        #Saturate bottom of range
        if fingerForce < 40:
            adjustedFingerForce = 0
        else:
            adjustedFingerForce = fingerForce
        speed = int(maprange((0,500),(1500,1700),adjustedFingerForce))
        set_rc_channel_pwm(5,speed)

        #Set some deadzone
        deadzone = 50
        if (fingerPos < posZero+deadzone and fingerPos > fingerPos-deadzone) or fingerForce<40:
            adjustedFingerPos = posZero
        else:
            adjustedFingerPos = fingerPos
        turn = int(maprange((0,posZero*2),(1400,1600),fingerPos))             #Need to tune turn rate
        if turn<1100:
            turn=1100
        print(fingerPos)
        set_rc_channel_pwm(4,turn)
        #print((fingerPos,turn))

    if saveData:
        expTime = time.time() - startTime
        #Check failure conditions
        if (expTime) > 120:
            runFail = True
            failReason = 'timeout'
            print('FAIL - timeout')
        
        log={
            'time': time.perf_counter()-startTimePC,
            'xacc': msg["xacc"],
            'yacc': msg["yacc"],
            'zacc': msg["zacc"],
            'xgyro': msg["xgyro"],
            'ygyro': msg["ygyro"],
            'zgyro': msg["zgyro"],
            'fingerZero': posZero,
            'fingerPos': fingerPos,
            'fingerForce': fingerForce,
            'adjustedFingerPos': adjustedFingerPos,
            'adjustedFingerForce': adjustedFingerForce,
            'vibration': hapticsOut[0],
            'hardness': hapticsOut[1],
            'visualTranslation0': tvec[0],
            'visualTranslation1': tvec[1],
            'visualTranslation2': tvec[2],
            'visualRotation0': rvec[0],
            'visualRotation1': rvec[1],
            'visualRotation2': rvec[2],
            'avgxloc': avgx,
            'avgyloc': avgy,
            'avgzloc': avgz,
            'heading': avgyaw,
            'tgtDist': targetDist,
            'speedDemand': speed,
            'turnDemand': turn,
            'groundSpeed': gndspd,
            'depth': depth
        }
        logFile.write(json.dumps(log))
        logFile.write('\n')

    commandWindow["-X-"].update("{:0.2f}".format(avgx))
    commandWindow["-Y-"].update("{:0.2f}".format(avgy))
    commandWindow["-Z-"].update("{:0.2f}".format(avgz))

    commandWindow["-TGT-"].update("{:0.2f}".format(targetDist))
    commandWindow["-YAW-"].update("{:0.2f}".format(avgyaw))
    commandWindow["-SPD-"].update("{:0.2f}".format(gndspd))


    commandWindow["-DEPTH-"].update("{:0.2f}".format(depth))
    commandWindow["-TIME-"].update("{:0.2f}".format(expTime))

    commandWindow["-BATT-"].update(f"{batteryLife}%")


    #Process events for user window (just handle exiting)
    event, values = userWindow.read(timeout=0)
    if event in ('Exit', None):
        break
    
    #Process events for command window
    event, values = commandWindow.read(timeout=0)
    if (values["-tag-"] != '') and (values["-tag-"] != '0.') and (float(values["-tag-"]) > 0.0):
        tagSize = float(values["-tag-"])
    #print(tagSize)

    if event in ('Exit', None):
        break

    if event == 'Start':
        print("Saving video/data")
        commandWindow['Start'].update(disabled=True)
        commandWindow['Pass'].update(disabled=False)
        commandWindow['Fail'].update(disabled=False)
        #generate log filenames
        posZero = fingerPos
        print(f"Touchpad zero set to {posZero}")
        startTime = time.time()
        startYaw = avgyaw
        if values["-condH-"]:
            conditionString = "Haptics"
        elif values["-condC-"]:
            conditionString = "NoHaptics"
        elif values["-condN-"]:
            conditionString = "NoCurrent"
        elif values["-condT-"]:
            conditionString = "Training"
        participant = values["-PID-"]
        repeat = int(values["-rep-"])
        dataFilename = f"logs/PID_{participant}_CONDITION_{conditionString}_REPEAT_{repeat}_TIME_{time.ctime(startTime)}.txt"
        rawVideoFilename = f"logs/PID_{participant}_CONDITION_{conditionString}_REPEAT_{repeat}_TIME_{time.ctime(startTime)}_raw.avi"
        markupVideoFilename = f"logs/PID_{participant}_CONDITION_{conditionString}_REPEAT_{repeat}_TIME_{time.ctime(startTime)}_markup.avi"
        #print(dataFilename)
        #print(videoFilename)
        fps=17.4
        rawVideoLog = cv2.VideoWriter(rawVideoFilename, cv2.VideoWriter_fourcc(*'MJPG'), fps, (1280,720))      #'test_video.avi'
        markupVideoLog = cv2.VideoWriter(markupVideoFilename, cv2.VideoWriter_fourcc(*'MJPG'), fps, (1280,720))      #'test_video.avi'
        logFile = open(dataFilename, 'w')
        logFile.write(f"{time.perf_counter()}: Trial conducted on: {time.ctime(startTime)}\n")
        logFile.write(f"{time.perf_counter()}: Initial heading: {startYaw}\n")
        saveVideo = True
        saveData = True
        SetLED(commandWindow,"-LOG-","green1")
        startTimePC = time.perf_counter()

    elif event == 'Pass':
        logFile.write('PASS\n')
        logFile.write(f"{time.perf_counter()}: Trial concluded at: {time.ctime(time.time())}\n")
        print("No longer saving video/Data")
        commandWindow['Start'].update(disabled=False)
        commandWindow['Pass'].update(disabled=True)
        commandWindow['Fail'].update(disabled=True)
        rawVideoLog.release()
        markupVideoLog.release()
        logFile.close()
        saveVideo = False
        saveData = False
        SetLED(commandWindow,"-LOG-","#004665")
        repeat = repeat + 1
        commandWindow["-rep-"].update(f"{repeat}")
        commandWindow.refresh()

    elif event == 'Fail' or runFail:
        runFail = False
        logFile.write("FAIL - "+failReason+"\n")
        logFile.write(f"{time.perf_counter()}: Trial concluded at: {time.ctime(time.time())}\n")
        print("No longer saving video/Data")
        commandWindow['Start'].update(disabled=False)
        commandWindow['Pass'].update(disabled=True)
        commandWindow['Fail'].update(disabled=True)
        rawVideoLog.release()
        markupVideoLog.release()
        logFile.close()
        saveVideo = False
        saveData = False
        SetLED(commandWindow,"-LOG-","#004665")
        repeat = repeat + 1
        commandWindow["-rep-"].update(f"{repeat}")
        commandWindow.refresh()
        failReason = 'Unspecified'

    elif event == 'Arm':
        commandWindow['Confirm'].update(disabled=False)

    elif event == 'Confirm':
        clearMotion()
        # Arm
        # master.arducopter_arm() or:
        master.mav.command_long_send(
            master.target_system,
            master.target_component,
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
            0,
            1, 21196, 0, 0, 0, 0, 0)

        # wait until arming confirmed (can manually check with master.motors_armed())
        print("Waiting for the vehicle to arm...")
        master.motors_armed_wait()
        lightOn(master)
        print('Armed!')
        if saveData:
            logFile.write(f"{time.perf_counter()}: Armed!")
        SetLED(commandWindow,"-ARM-","red")              #use red for on
        commandWindow['Arm'].update(disabled=True)
        commandWindow['Confirm'].update(disabled=True)
        commandWindow['Disarm'].update(disabled=False)
        commandWindow['Forward'].update(disabled=False)
        commandWindow['Reverse'].update(disabled=False)
        commandWindow['Left'].update(disabled=False)
        commandWindow['Right'].update(disabled=False)
        commandWindow['All Stop'].update(disabled=False)
        commandWindow['Touchpad'].update(disabled=False)
        commandWindow['Up'].update(disabled=False)
        commandWindow['Down'].update(disabled=False)
        commandWindow['StrafeL'].update(disabled=False)
        commandWindow['StrafeR'].update(disabled=False)
        commandWindow['Straight'].update(disabled=False)
        commandWindow['Manual'].update(disabled=False)
        commandWindow['Stabilize'].update(disabled=False)
        commandWindow.refresh()
        
    elif event == 'Disarm':
        clearMotion()
        # Disarm
        # master.arducopter_disarm() or:
        master.mav.command_long_send(
            master.target_system,
            master.target_component,
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
            0,
            0, 21196, 0, 0, 0, 0, 0)

        # wait until disarming confirmed
        print("Waiting for the vehicle to disarm...")
        master.motors_disarmed_wait()
        print('Disarmed!')
        lightOff(master)
        if saveData:
            logFile.write(f"{time.perf_counter()}: Disarmed!")
        SetLED(commandWindow,"-ARM-","#460065")              #use red for on
        commandWindow['Arm'].update(disabled=False)
        commandWindow['Disarm'].update(disabled=True)
        commandWindow['Forward'].update(disabled=True)
        commandWindow['Reverse'].update(disabled=True)
        commandWindow['Left'].update(disabled=True)
        commandWindow['Right'].update(disabled=True)
        commandWindow['All Stop'].update(disabled=True)
        commandWindow['Touchpad'].update(disabled=True)
        commandWindow['Up'].update(disabled=True)
        commandWindow['Down'].update(disabled=True)
        commandWindow['StrafeL'].update(disabled=True)
        commandWindow['StrafeR'].update(disabled=True)
        commandWindow['Straight'].update(disabled=True)
        commandWindow.refresh()
    
    elif event == 'All Stop':
        print("stop")
        clearMotion()
    
    elif event == 'Forward':
        # Set some forward
        print("Forward")
        set_rc_channel_pwm(5, 1550) #Channel 5 for forward

    elif event == 'Reverse':
        # Set some backward
        print("Reverse")
        set_rc_channel_pwm(5, 1450)

    elif event == 'Left':
        # Set some forward
        print("Left")
        set_rc_channel_pwm(4, 1450) #Channel 5 for forward

    elif event == 'Right':
        # Set some backward
        print("Right")
        set_rc_channel_pwm(4, 1550)

    elif event == 'Up':
        # Set some forward
        print("Up")
        set_rc_channel_pwm(3, 1550) #Channel 5 for forward

    elif event == 'Down':
        # Set some backward
        print("Down")
        set_rc_channel_pwm(3, 1450)

    elif event == 'StrafeL':
        # Set some forward
        print("Left")
        set_rc_channel_pwm(6, 1450) #Channel 5 for forward

    elif event == 'StrafeR':
        # Set some backward
        print("Right")
        set_rc_channel_pwm(6, 1550)

    elif event == 'Straight':
        # Set some backward
        print("Straighten")
        set_rc_channel_pwm(3, 1500)
        set_rc_channel_pwm(4, 1500)
        set_rc_channel_pwm(6, 1500)

    elif event == 'Manual':
        # Choose a mode
        mode = 'MANUAL'     #DEPTH_HOLD

        # Check if mode is available
        if mode not in master.mode_mapping():
            print('Unknown mode : {}'.format(mode))
            print('Try:', list(master.mode_mapping().keys()))
            sys.exit(1)

        # Get mode ID
        mode_id = master.mode_mapping()[mode]
        print(f'I know that mode: {mode_id}')
        # Set new mode
        # master.mav.command_long_send(
        #    master.target_system, master.target_component,
        #    mavutil.mavlink.MAV_CMD_DO_SET_MODE, 0,
        #    0, mode_id, 0, 0, 0, 0, 0) or:

        master.set_mode(mode_id)
        ##master.mav.set_mode_send(
        #    master.target_system,
        #    mavutil.mavlink.MAV_CMD_DO_SET_MODE,
        #    mode_id)


        print('Sent mode message')
        while True:
            # Wait for ACK command
            # Would be good to add mechanism to avoid endlessly blocking
            # if the autopilot sends a NACK or never receives the message
            ack_msg = master.recv_match(type='COMMAND_ACK', blocking=True)
            ack_msg = ack_msg.to_dict()

            # Continue waiting if the acknowledged command is not `set_mode`
            if ack_msg['command'] != mavutil.mavlink.MAV_CMD_DO_SET_MODE:
                continue

            # Print the ACK result !
            print(mavutil.mavlink.enums['MAV_RESULT'][ack_msg['result']].description)
            break

        master.wait_heartbeat()
        commandWindow['Stabilize'].update(disabled=False)
        commandWindow['Manual'].update(disabled=True)

    elif event == 'Stabilize':
        # Choose a mode
        mode = 'ALT_HOLD'     #DEPTH_HOLD

        # Check if mode is available
        if mode not in master.mode_mapping():
            print('Unknown mode : {}'.format(mode))
            print('Try:', list(master.mode_mapping().keys()))
            sys.exit(1)

        # Get mode ID
        mode_id = master.mode_mapping()[mode]
        print(f'I know that mode: {mode_id}')
        # Set new mode
        # master.mav.command_long_send(
        #    master.target_system, master.target_component,
        #    mavutil.mavlink.MAV_CMD_DO_SET_MODE, 0,
        #    0, mode_id, 0, 0, 0, 0, 0) or:

        master.set_mode(mode_id)
        ##master.mav.set_mode_send(
        #    master.target_system,
        #    mavutil.mavlink.MAV_CMD_DO_SET_MODE,
        #    mode_id)


        print('Sent mode message')
        while True:
            # Wait for ACK command
            # Would be good to add mechanism to avoid endlessly blocking
            # if the autopilot sends a NACK or never receives the message
            ack_msg = master.recv_match(type='COMMAND_ACK', blocking=True)
            ack_msg = ack_msg.to_dict()

            # Continue waiting if the acknowledged command is not `set_mode`
            if ack_msg['command'] != mavutil.mavlink.MAV_CMD_DO_SET_MODE:
                continue

            # Print the ACK result !
            print(mavutil.mavlink.enums['MAV_RESULT'][ack_msg['result']].description)
            break

        master.wait_heartbeat()
        commandWindow['Manual'].update(disabled=False)
        commandWindow['Stabilize'].update(disabled=True)

    elif event == 'Touchpad':
        if touchControlEnabled:
            touchControlEnabled = False
            SetLED(commandWindow,"-TOUCH-","#004665")
        else:
            touchControlEnabled = True
            SetLED(commandWindow,"-TOUCH-","green1")


    elif event == 'Start vibration':
        hapticsOut[0] = 1                           #Signal to start vibrating
        SetLED(commandWindow,"-VIBE-","green1")

    elif event == 'Stop vibration':
        hapticsOut[0] = 0                           #Signal to stop vibrating
        SetLED(commandWindow,"-VIBE-","#004665")

    elif event == 'Go hard':
        hapticsOut[1] = 500                         #Signal max hardness
        SetLED(commandWindow,"-HARD-","green1")

    elif event == 'Go soft':
        hapticsOut[1] = 0                              #Signal min hardness
        SetLED(commandWindow,"-HARD-","#004665")

    elif event == 'Zero':
        posZero = fingerPos
        print(f"Touchpad zero set to {posZero}")

    elif event == 'Print':
        print((fingerPos,fingerForce))

    elif event == 'Raw still':
        cv2.imwrite('ROVCam_raw_'+str(time.time())+'.jpg', frame)

    elif event == 'Circle still':
        cv2.imwrite('ROVCam_circle_'+str(time.time())+'.jpg', circleFrame)

    elif event == 'CV still':
        cv2.imwrite('ROVCam_cv_'+str(time.time())+'.jpg', tagFrame)

    elif event == 'Ready to start':
        print("Start pressed")
        threading.Thread(target=lightSignal, args=(master,1,0)).start()
        #lightSignal(master,1,0)

    elif event == 'Ready to end':
        print("End pressed")
        threading.Thread(target=lightSignal, args=(master,2,0)).start()

    elif event == 'Move area':
        print("Change area pressed")
        threading.Thread(target=lightSignal, args=(master,3,0)).start()

    elif event == 'EMERGENCY':
        print("Emergency pressed")
        userWindow.close()
        threading.Thread(target=lightSignal, args=(master,1000,0)).start()

haptics.close()
rawVideoLog.release()
markupVideoLog.release()
userWindow.close()
commandWindow.close()

