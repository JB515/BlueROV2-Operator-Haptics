# BlueROV2-Operator-Haptics
Operator station software for the BlueROV 2 with haptic feedback

This example software uses gstreamer, and OpenCV to load, process and save video from the BlueROV 2 and pymavlink to send motion and lighting commands to the ROV. The UI was built with pysimplegui. Note that the software will not work as intended without my soft haptic touchpad (or at least some other device listening on the same IP address). The networking code that communicates with the touchpad can be safely commented/deleted to restore this. The software will also not load without a BlueROV2 connected, though should give terminal feedback to indicate this.
