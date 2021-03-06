
import argparse

import time

import msgpack

from enum import Enum, auto

import numpy as np

from planning_utils import a_star, heuristic, create_grid, coll_prune_path

from udacidrone import Drone

from udacidrone.connection import MavlinkConnection

from udacidrone.messaging import MsgID

from udacidrone.frame_utils import global_to_local


class States(Enum):

    MANUAL = auto()

    ARMING = auto()

    TAKEOFF = auto()

    WAYPOINT = auto()

    LANDING = auto()

    DISARMING = auto()

    PLANNING = auto()



class MotionPlanning(Drone):

    def __init__(self, connection):

        super().__init__(connection)

        goal_global = [-122.398249,37.796079,0]

        self.target_position = np.array([0.0, 0.0, 0.0])

        self.waypoints = []

        self.in_mission = True

        self.check_state = {}


        # initial state

        self.flight_state = States.MANUAL
        self.goal_global = goal_global

        # register all your callbacks here

        self.register_callback(MsgID.LOCAL_POSITION, self.local_position_callback)

        self.register_callback(MsgID.LOCAL_VELOCITY, self.velocity_callback)

        self.register_callback(MsgID.STATE, self.state_callback)


    def local_position_callback(self):

        if self.flight_state == States.TAKEOFF:

            if -1.0 * self.local_position[2] > 0.95 * self.target_position[2]:

                self.waypoint_transition()

        elif self.flight_state == States.WAYPOINT:

            if np.linalg.norm(self.target_position[0:2] - self.local_position[0:2]) < 1.0:

                if len(self.waypoints) > 0:

                    self.waypoint_transition()

                else:

                    if np.linalg.norm(self.local_velocity[0:2]) < 1.0:

                        self.landing_transition()


    def velocity_callback(self):

        if self.flight_state == States.LANDING:

            if self.global_position[2] - self.global_home[2] < 0.1:

                if abs(self.local_position[2]) < 0.01:

                    self.disarming_transition()


    def state_callback(self):

        if self.in_mission:

            if self.flight_state == States.MANUAL:

                self.arming_transition()

            elif self.flight_state == States.ARMING:

                if self.armed:

                    self.plan_path()

            elif self.flight_state == States.PLANNING:

                self.takeoff_transition()

            elif self.flight_state == States.DISARMING:

                if ~self.armed & ~self.guided:

                    self.manual_transition()


    def arming_transition(self):

        self.flight_state = States.ARMING

        print("arming transition")

        self.arm()

        self.take_control()


    def takeoff_transition(self):

        self.flight_state = States.TAKEOFF

        print("takeoff transition")

        self.takeoff(self.target_position[2])


    def waypoint_transition(self):

        self.flight_state = States.WAYPOINT

        print("waypoint transition")

        self.target_position = self.waypoints.pop(0)

        print('target position', self.target_position)

        self.cmd_position(self.target_position[0], self.target_position[1], self.target_position[2], self.target_position[3])


    def landing_transition(self):

        self.flight_state = States.LANDING

        print("landing transition")

        self.land()


    def disarming_transition(self):

        self.flight_state = States.DISARMING

        print("disarm transition")

        self.disarm()

        self.release_control()


    def manual_transition(self):

        self.flight_state = States.MANUAL

        print("manual transition")

        self.stop()

        self.in_mission = False


    def send_waypoints(self):

        print("Sending waypoints to simulator ...")

        data = msgpack.dumps(self.waypoints)

        self.connection._master.write(data)


    def plan_path(self):

        self.flight_state = States.PLANNING

        print("Searching for a path ...")

        TARGET_ALTITUDE = 5

        SAFETY_DISTANCE = 5

        self.target_position[2] = TARGET_ALTITUDE

        # read lat0, lon0 from colliders into floating point values
		# height of 0

		# file to read coordinates

        starting_location = "colliders.csv"

        # sample result lat0 37.792480, lon0 -122.397450
		# open file

        latlon_file_read = open(starting_location, "r")

		# Read first line only

        latlon = latlon_file_read.readline().strip()

		# remove text and split list into latitude and longitude

        list_out = latlon.replace("lat0","").replace(" lon0","").split(",")

		# iterate through list_out and provide in floats to map_out

        map_out = map(float,list_out)

		# provide in the form of a list

        list_out_float = list(map_out)

		# retrieve lat0 then lon0 from the list

        lat0 = list_out_float[0]
        lon0 = list_out_float[1]

		# for debugging
        #print('lat0 {0}, lon0 {1}'.format(lat0,lon0))

        # set home position to (lon0, lat0, 0)

        self.set_home_position(lon0,lat0,0)

        # retrieve current global position

        current_global_position = self.global_position

        # convert to current local position using global_to_local()

        current_local_position = global_to_local(self.global_position, self.global_home)
        goal_local_position = global_to_local(self.goal_global, self.global_home)

        # Read in obstacle map

        data = np.loadtxt('colliders.csv', delimiter=',',dtype='Float64',skiprows=2)

        # Define a grid for a particular altitude and safety margin around obstacles

        grid, north_offset, east_offset = create_grid(data, TARGET_ALTITUDE, SAFETY_DISTANCE)

        # Define starting point on the grid (this is just grid center)

        grid_start = (int(np.ceil(current_local_position[0]-north_offset)), int(np.ceil(current_local_position[1]-east_offset)))

        # Set goal as some arbitrary position on the grid

        grid_goal = (-north_offset + 15, -east_offset + 15)

        # adapt to set goal as latitude / longitude position and convert
        gp_goal_gps = global_to_local(self.goal_global, self.global_home)
        grid_goal = (int(np.ceil(gp_goal_gps[0] - north_offset)), int(np.ceil(gp_goal_gps[1] - east_offset)))


        print("a_star")
        path, _ = a_star(grid, heuristic, grid_start, grid_goal)

        # prune path to minimize number of waypoints
        print("prune")
        #print("In Path = {0}".format(path))
        path=coll_prune_path(path)
        #print("Out Path = {0}".format(path))

        # (if you're feeling ambitious): Try a different approach altogether!

		# Convert path to waypoints
        print("path to waypoints")
        waypoints = [[p[0] + north_offset, p[1] + east_offset, TARGET_ALTITUDE, 0] for p in path]

        # Set self.waypoints

        self.waypoints = waypoints

        # send waypoints to sim (this is just for visualization of waypoints)
        print("sent the waypoints")
        self.send_waypoints()

        def start(self):

            self.start_log("Logs", "NavLog.txt")
            print("starting connection")
            self.connection.start()


        # Only required if they do threaded

        # while self.in_mission:

        #    pass

        #self.stop_log()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=5760, help='Port number')
    parser.add_argument('--host', type=str, default='127.0.0.1', help="host address, i.e. '127.0.0.1'")
    args = parser.parse_args()
    conn = MavlinkConnection('tcp:{0}:{1}'.format(args.host, args.port), timeout=60)
    drone = MotionPlanning(conn)
    time.sleep(1)
    drone.start()
