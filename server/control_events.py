# Events and associated callbacks for server-side sockets

from flask import session
from flask.ext.socketio import emit, join_room, leave_room
import json
from threading import Thread

import mission_state
import navigation
from server_state import socketio

from dronekit import connect, VehicleMode, LocationGlobalRelative
import time
import sys
from pymavlink import mavutil
import eventlet
eventlet.monkey_patch()

import math

# TODO: namespace
CONTROL_NAMESPACE = "/"

@socketio.on('connect', namespace=CONTROL_NAMESPACE)
def on_connect():
		print "[socket][control][connect]: Connection received"
		eventlet.spawn(listen_for_location_change, [mission_state.vehicle.location.global_relative_frame])


# Updates the location of the drone on a 1 Hz cycle
def listen_for_location_change(vehicle_location_param):
		vehicle_location = vehicle_location_param[0]
		while True:
			current_lat = mission_state.vehicle.location.global_relative_frame.lat
			current_lon = mission_state.vehicle.location.global_relative_frame.lon
			current_alt = mission_state.vehicle.location.global_relative_frame.alt
			if (vehicle_location.lat != current_lat) or (vehicle_location.lon != current_lon) or (vehicle_location.alt != current_alt):
						loc = {'lat': current_lat,
									 'lon': current_lon,
									 'alt': current_alt}
						json_loc = json.dumps(loc)
						print "[socket][control][gps_pos]: ", str(json_loc)
						socketio.emit("gps_pos_ack", json_loc, broadcast=True)
			eventlet.sleep(1)

@socketio.on('gps_pos_tango') # , namespace=CONTROL_NAMESPACE)
def gpsChangeTango(json):
	print "[socket][control][gps_pos]: " + str(json)
	emit("gps_pos_ack", json, broadcast=True)

@socketio.on('gps_pos') # , namespace=CONTROL_NAMESPACE)
def gpsChange(json):
		loc = json
		#global gps_init
		#if gps_init == False:
		#	navigation.getOrigin(json)
		#	gps_init = True
		print "[socket][control][gps_pos]: " + str(json)
		emit("gps_pos_ack", json, broadcast=True)

STEP = 0.00003
RADIAL_OFFSETS = [(1, 0), (1, 1), (-1, 1), (-1, -1), (2, -1), (2, 2), (-2, 2), (-2, -2), (3, -2), (3, 3)]
LINE_OFFSETS = [(0, 1), (-4, 1), (-4, 2), (0, 2), (0, 3), (-4, 3), (-4, 4), (0, 4), (0, 5), (-4, 5)]
SECTOR_OFFSETS = [(4, 0), (3, -1), (1, 1), (2, 2), (2, -2), (1, -1), (3, 1)]

@socketio.on('sar_path')
def flySARPath(json):
	vehicle = mission_state.vehicle
	lat = json['lat']
	lon = json['lon']
	altitude = json['altitude']
	path_type = json['sar_type']
	waypoint_list = [(lat, lon, altitude)]	
	if path_type == 'line':
		for waypoint in LINE_OFFSETS:
			waypoint_list.append((float(lat) + waypoint[0]  * STEP, float(lon) + waypoint[1]  * STEP, altitude))
	elif path_type == 'sector':
		for waypoint in SECTOR_OFFSETS:
			waypoint_list.append((float(lat) + waypoint[0]  * STEP, float(lon) + waypoint[1]  * STEP, altitude))
	elif path_type == 'radial':
		for waypoint in RADIAL_OFFSETS:
			waypoint_list.append((float(lat) + waypoint[0] * STEP, float(lon) + waypoint[1]  * STEP, altitude))

	# TODO: Call dronekit gps waypoint flight command with list of waypoints
	for wp in waypoint_list:
		wp_lat = wp[0]
		wp_lng = wp[1]
		wp_alt = wp[2] 
		waypoint_location = LocationGlobalRelative(wp_lat, wp_lon, wp_alt)
	  vehicle.simple_goto(waypoint_location)
		while ((abs(vehicle.location.global_relative_frame.lat - wp_lat) > STEP/3) and
					 (abs(vehicle.location.global_relative_frame.lng - wp_lng) > STEP/3) and
					 (abs(vehicle.location.global_relative_frame.alt - wp_alt) > 0.1)): 
			time.sleep(0.1)


@socketio.on('altitude_abs_cmd') # , namespace=CONTROL_NAMESPACE)
def altitudeAbsChange(json):
		print "[socket][control][altitude]: " + str(json)
		target_alt = float(json['altitude'])
		change_altitude_global(target_alt)

@socketio.on('altitude_cmd')
def altitudeChange(json):
		vehicle = mission_state.vehicle
		print "[socket][control][altitude]: " + str(json)
		dalt = float(json['dalt'])
		curr_alt = vehicle.location.global_relative_frame.alt
		change_altitude_global(curr_alt + dalt)

@socketio.on('rotation_cmd') # , namespace=CONTROL_NAMESPACE)
def rotationChange(json):
		print "[socket][control][rotation]: " + str(json)
		heading = float(json['heading'])
		condition_yaw(heading)

@socketio.on('waypoint_cmd')
def waypointCommand(json):
	vehicle = mission_state.vehicle
	#vehicle.airspeed = 4
	print "airspeed: ", vehicle.airspeed
	print "[socket][control][waypoint]: " + str(json)
	lat = float(json['lat'])
	lon = float(json['lon'])
	if 'alt' in json:
		alt = float(json['alt'])
	else:
		alt = vehicle.location.global_relative_frame.alt
	waypoint_location = LocationGlobalRelative(lat, lon, alt)
	vehicle.simple_goto(waypoint_location)

@socketio.on('lateral_cmd') #, namespace=CONTROL_NAMESPACE)
def lateralChangeDiscrete(json):
	print "[socket][control][lateral]: " + str(json)
	direction = json['direction']
	# args are x_vel, y_vel, z_vel, duration
	if direction == "left":
		send_ned_velocity(-1, 0, 0, 10)
	elif direction == "right":
		send_ned_velocity(1, 0, 0, 10)
	elif direction == "forward":
		send_ned_velocity(0, -1, 0, 10)
	elif direction == "back":
		send_ned_velocity(0, 1, 0, 10)
	elif direction == "stop":
		send_ned_velocity(0, 0, 0, 1)


def lateralChangeJoystick(json):
		print "[socket][control][lateral]: " + str(json)
		x_offset = json['x_offset']
		y_offset = json['y_offset']

		if x_offset == 0 and y_offset == 0:
				return

		magnitude = math.sqrt(x_offset ** 2 + y_offset ** 2)

		x_norm = x_offset / magnitude
		y_norm = y_offset / magnitude

		speed = 5

		x_vel = speed * x_norm
		y_vel = speed * y_norm

		duration = json['duration']
		send_ned_velocity(x_vel, y_vel, 0, duration)


def condition_yaw(heading, relative=True):
		vehicle = mission_state.vehicle

		direction = 1
		if heading < 0:
			direction = -1

		send_global_velocity(0, 0, 0, 1)
		if relative:
				is_relative=1 #yaw relative to direction of travel
		else:
				is_relative=0 #yaw is an absolute angle
		# create the CONDITION_YAW command using command_long_encode()
		msg = vehicle.message_factory.command_long_encode(
				0, 0,		 # target system, target component
				mavutil.mavlink.MAV_CMD_CONDITION_YAW, #command
				0, #confirmation
				abs(heading),		# param 1, yaw in degrees. Makes this a positive value
				0,					# param 2, yaw speed deg/s
				direction,					# param 3, direction -1 ccw, 1 cw
				is_relative, # param 4, relative offset 1, absolute angle 0
				0, 0, 0)		# param 5 ~ 7 not used
		# send command to vehicle
		vehicle.send_mavlink(msg)

		yaw_before = vehicle.attitude.yaw
		target_yaw = 0
		if relative:
			desired_yaw_change = math.radians(heading)
			target_yaw = yaw_before + desired_yaw_change
		else:
			target_yaw = math.radians(heading)

		#while abs(vehicle.attitude.yaw - target_yaw) > 0.01:
		#	pass


def send_ned_velocity(velocity_x, velocity_y, velocity_z, duration):
		"""
		Move vehicle in direction based on specified velocity vectors.
		"""
		vehicle = mission_state.vehicle
		msg = vehicle.message_factory.set_position_target_local_ned_encode(
				0,			 # time_boot_ms (not used)
				0, 0,		 # target system, target component
				mavutil.mavlink.MAV_FRAME_BODY_NED, # frame
				0b0000111111000111, # type_mask (only speeds enabled)
				0, 0, 0, # x, y, z positions (not used)
				velocity_x, velocity_y, velocity_z, # x, y, z velocity in m/s
				0, 0, 0, # x, y, z acceleration (not supported yet, ignored in GCS_Mavlink)
				0, 0)		 # yaw, yaw_rate (not supported yet, ignored in GCS_Mavlink)


		# send command to vehicle on 1 Hz cycle
		for x in range(0, duration):
			vehicle.send_mavlink(msg)
			time.sleep(1)


def send_global_velocity(velocity_x, velocity_y, velocity_z, duration):
		"""
		Move vehicle in direction based on specified velocity vectors.
		"""
		vehicle = mission_state.vehicle
		msg = vehicle.message_factory.set_position_target_global_int_encode(
				0,			 # time_boot_ms (not used)
				0, 0,		 # target system, target component
				mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT, # frame
				0b0000111111000111, # type_mask (only speeds enabled)
				0, # lat_int - X Position in WGS84 frame in 1e7 * meters
				0, # lon_int - Y Position in WGS84 frame in 1e7 * meters
				0, # alt - Altitude in meters in AMSL altitude(not WGS84 if absolute or relative)
				# altitude above terrain if GLOBAL_TERRAIN_ALT_INT
				velocity_x, # X velocity in NED frame in m/s
				velocity_y, # Y velocity in NED frame in m/s
				velocity_z, # Z velocity in NED frame in m/s
				0, 0, 0, # afx, afy, afz acceleration (not supported yet, ignored in GCS_Mavlink)
				0, 0)		 # yaw, yaw_rate (not supported yet, ignored in GCS_Mavlink)

		# send command to vehicle on 1 Hz cycle
		for x in range(0, duration):
				vehicle.send_mavlink(msg)
				time.sleep(1)


def change_altitude_global(target_alt):
		vehicle = mission_state.vehicle
		target_location = LocationGlobalRelative(vehicle.location.global_relative_frame.lat,
																						 vehicle.location.global_relative_frame.lon,
																						 target_alt)
		vehicle.simple_goto(target_location)

		while abs(target_alt - vehicle.location.global_relative_frame.alt) > 0.1:
			time.sleep(0.5)	

