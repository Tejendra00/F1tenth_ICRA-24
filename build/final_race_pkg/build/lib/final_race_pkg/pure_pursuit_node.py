#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
# from tf_transformations import euler_from_quaternion
import math

import numpy as np
from sensor_msgs.msg import LaserScan
from ackermann_msgs.msg import AckermannDriveStamped, AckermannDrive
from geometry_msgs.msg import PoseStamped, Pose, PoseArray
from nav_msgs.msg import Odometry
from visualization_msgs.msg import Marker, MarkerArray
from geometry_msgs.msg import Point
from std_msgs.msg import ColorRGBA
from .util import *

# get the file path for this package
csv_loc = '/home/ros2/F1tenth-Final-Race-Agent-and-Toolbox/curve1.csv'

#  Constants from xacro
WIDTH = 0.2032  # (m)
WHEEL_LENGTH = 0.0381  # (m)
MAX_STEER = 0.48  # (rad)

class PurePursuit(Node):
    """ 
    Implement Pure Pursuit on the car
    This is just a template, you are free to implement your own node!
    """

    def __init__(self):
        super().__init__('pure_pursuit_node')

        # Params
        # self.declare_parameter('if_real', False)
        self.declare_parameter('lookahead_distance', 1.5)
        self.declare_parameter('lookahead_points', 12)      # to calculate yaw diff
        self.declare_parameter('lookbehind_points', 2)      # to eliminate the influence of latency
        self.declare_parameter('L_slope_atten', 0.7)        # attenuate lookahead distance with large yaw, (larger: smaller L when turning)

        self.declare_parameter('kp', 0.6)
        self.declare_parameter('ki', 0.0)
        self.declare_parameter('kd', 0.005)
        self.declare_parameter("max_control", MAX_STEER)
        self.declare_parameter("steer_alpha", 1.0)
        self.declare_parameter("num_lanes")
        self.declare_parameter('avoid_v_diff')
        self.declare_parameter('avoid_L_scale')
        self.declare_parameter('pred_v_buffer')
        self.declare_parameter('avoid_buffer')
        self.declare_parameter('avoid_span')
        self.declare_parameter("lane_occupied_dist")
        self.declare_parameter("obs_activate_dist")

        print("finished declaring parameters")
        # PID Control Params
        self.prev_error = 0.0
        self.integral = 0.0
        self.prev_steer = 0.0
        
        self.flag = False
        print("if real world test? ", self.flag)

        # TODO: Get target x and y from pre-calculated waypoints
        waypoints = np.loadtxt(csv_loc, delimiter=',',skiprows=1)
        self.x_list = waypoints[:, 0]
        self.y_list = waypoints[:, 1]
        self.v_list = waypoints[:, 2]
        self.xyv_list = waypoints[:, 0:2]   # (x,y,v)
        self.yaw_list = waypoints[:, 3]
        self.v_max = np.max(self.v_list)
        self.v_min = np.min(self.v_list)
        print("finished loading waypoints")

        self.num_lanes = self.get_parameter("num_lanes").get_parameter_value().integer_value

        self.obstacles = None
        self.opponent = np.array([np.inf, np.inf])
        self.lane_free = [True] * self.num_lanes
        self.declare_parameter('avoid_dist')
        self.opponent_v = 0.0
        self.opponent_last = np.array([0.0, 0.0])
        self.opponent_timestamp = 0.0
        self.pred_v_buffer = self.get_parameter('pred_v_buffer').get_parameter_value().integer_value
        self.pred_v_counter = 0
        self.avoid_buffer = self.get_parameter('avoid_buffer').get_parameter_value().integer_value
        self.avoid_counter = 0
        self.detect_oppo = False
        self.avoid_L_scale = self.get_parameter('avoid_L_scale').get_parameter_value().double_value
        self.last_lane = -1
        

        

        # Topics & Subs, Pubs
        
        if self.flag == True:  
            odom_topic = '/pf/viz/inferred_pose'
            self.odom_sub_ = self.create_subscription(PoseStamped, odom_topic, self.pose_callback, 10)
            print(odom_topic + " initialized")
        else:
            odom_topic = '/ego_racecar/odom'
            self.odom_sub_ = self.create_subscription(Odometry, odom_topic, self.pose_callback, 10)
            print(odom_topic + " initialized")
        drive_topic = '/drive'
        waypoint_topic = '/waypoint'
        waypoint_path_topic = '/waypoint_path'
        obstacle_topic = "/opp_predict/bbox"
        opponent_topic = "/opp_predict/state"

        self.obstacle_sub_ = self.create_subscription(PoseArray, obstacle_topic, self.obstacle_callback, 1)
        self.opponent_sub_ = self.create_subscription(PoseStamped, opponent_topic, self.opponent_callback, 1)

        self.traj_published = False
        self.drive_pub_ = self.create_publisher(AckermannDriveStamped, drive_topic, 10)
        print("drive_pub_ initialized, topic: " + drive_topic)
        self.waypoint_pub_ = self.create_publisher(Marker, waypoint_topic, 10)
        print("waypoint_pub_ initialized, topic: " + waypoint_topic)
        self.waypoint_path_pub_ = self.create_publisher(Marker, waypoint_path_topic, 10)
        print("waypoint_path_pub_ initialized, topic: " + waypoint_path_topic)
       
        
        
########################################### Callback ############################################

    def obstacle_callback(self, obstacle_msg: PoseArray):
        obstacle_list = []
        for obstacle in obstacle_msg.poses:
            x = obstacle.position.x
            y = obstacle.position.y
            obstacle_list.append([x, y])
        self.obstacles = np.array(obstacle_list) if obstacle_list else None

        if self.obstacles is None:
            self.lane_free = np.array([True] * self.num_lanes)
            return

        lane_occupied_dist = self.get_parameter("lane_occupied_dist").get_parameter_value().double_value
        for i in range(self.num_lanes):
            d = scipy.spatial.distance.cdist(self.lane_pos[i], self.obstacles)
            self.lane_free[i] = (np.min(d) > lane_occupied_dist)
        #print(f'lane_free_situation {self.lane_free}')


    def opponent_callback(self, opponent_msg: PoseStamped):
        opponent_x = opponent_msg.pose.position.x
        opponent_y = opponent_msg.pose.position.y
        self.opponent = np.array([opponent_x, opponent_y])
        # print(self.opponent)

        ## velocity
        if not np.any(np.isinf(self.opponent)):
            #print(self.detect_oppo)
            if self.detect_oppo:
                oppoent_dist_diff = np.linalg.norm(self.opponent - self.opponent_last)
                # self.opponent_v = 0.0
                # if oppoent_dist_diff != 0:
                if self.pred_v_counter == 7:
                    self.pred_v_counter = 0
                    cur_time = opponent_msg.header.stamp.nanosec/1e9 + opponent_msg.header.stamp.sec
                    time_interval = cur_time - self.opponent_timestamp
                    self.opponent_timestamp = cur_time
                    opponent_v = oppoent_dist_diff / max(time_interval, 0.005)
                    self.opponent_last = self.opponent.copy()
                    self.opponent_v = opponent_v
                    # print(f'cur distance diff {oppoent_dist_diff}')
                    print(f'cur oppoent v {self.opponent_v}')
                else:
                    self.pred_v_counter += 1
            else:
                self.detect_oppo = True
                self.opponent_last = self.opponent.copy()
        else:
            self.detect_oppo = False
        
    def pose_callback(self, pose_msg):
        if self.flag == True:  
            curr_x = pose_msg.pose.position.x
            curr_y = pose_msg.pose.position.y
            curr_quat = pose_msg.pose.orientation
        else:
            curr_x = pose_msg.pose.pose.position.x
            curr_y = pose_msg.pose.pose.position.y
            curr_quat = pose_msg.pose.pose.orientation
        curr_pos = np.array([curr_x, curr_y])
        curr_yaw = math.atan2(2 * (curr_quat.w * curr_quat.z + curr_quat.x * curr_quat.y),
                              1 - 2 * (curr_quat.y ** 2 + curr_quat.z ** 2))
        
        # change L based on another lookahead distance for yaw difference!
        L = self.get_parameter('lookahead_distance').get_parameter_value().double_value
        lookahead_points = self.get_parameter('lookahead_points').get_parameter_value().integer_value
        lookbehind_points = self.get_parameter('lookbehind_points').get_parameter_value().integer_value
        slope = self.get_parameter('L_slope_atten').get_parameter_value().double_value

        xyv_list = self.xyv_list
        yaw_list = self.yaw_list
        v_list = self.v_list

        error, target_v, target_point, curr_target_idx = get_lookahead(curr_pos, curr_yaw, xyv_list, yaw_list, v_list, L, lookahead_points, lookbehind_points, slope)

        self.target_point = target_point
        self.curr_target_idx = curr_target_idx
        
        # TODO: publish drive message, don't forget to limit the steering angle.
        message = AckermannDriveStamped()
        message.drive.speed = target_v
        message.drive.steering_angle = self.get_steer(error)
        #self.get_logger().info('speed: %f, steer: %f' % (target_v, self.get_steer(error)))
        self.drive_pub_.publish(message)

        # remember to visualize the waypoints
        self.visualize_waypoints()

####################################### Visualization ########################################

    def visualize_waypoints(self):
        # TODO: publish the waypoints properly
        marker = Marker()
        marker.header.frame_id = '/map'
        marker.id = 0
        marker.ns = 'pursuit_waypoint' + str(0)
        marker.type = 4
        marker.action = 0
        marker.points = []
        marker.colors = []
        length = self.x_list.shape[0]
        for i in range(length + 1):
            this_point = Point()
            this_point.x = self.x_list[i % length]
            this_point.y = self.y_list[i % length]
            marker.points.append(this_point)

            this_color = ColorRGBA()
            normalized_target_speed = (self.v_list[i % length] - self.v_min) / (self.v_max - self.v_min)
            this_color.a = 1.0
            this_color.r = (1 - normalized_target_speed)
            this_color.g = normalized_target_speed
            marker.colors.append(this_color)

        this_scale = 0.1
        marker.scale.x = this_scale
        marker.scale.y = this_scale
        marker.scale.z = this_scale

        marker.pose.orientation.w = 1.0

        self.waypoint_path_pub_.publish(marker)

        # also publish the target but larger
        marker = Marker()
        marker.header.frame_id = '/map'
        marker.id = 0
        marker.ns = 'pursuit_waypoint_target'
        marker.type = 1
        marker.action = 0
        marker.pose.position.x = self.target_point[0]
        marker.pose.position.y = self.target_point[1]

        normalized_target_speed = self.v_list[self.curr_target_idx] / self.v_max
        marker.color.a = 1.0
        marker.color.r = (1 - normalized_target_speed)
        marker.color.g = normalized_target_speed

        this_scale = 0.2
        marker.scale.x = this_scale
        marker.scale.y = this_scale
        marker.scale.z = this_scale

        marker.pose.orientation.w = 1.0

        self.waypoint_pub_.publish(marker)

    def get_steer(self, error):
        """ Get desired steering angle by PID
        """
        kp = self.get_parameter('kp').get_parameter_value().double_value
        ki = self.get_parameter('ki').get_parameter_value().double_value
        kd = self.get_parameter('kd').get_parameter_value().double_value
        max_control = self.get_parameter('max_control').get_parameter_value().double_value
        alpha = self.get_parameter('steer_alpha').get_parameter_value().double_value

        d_error = error - self.prev_error
        self.prev_error = error
        self.integral += error
        steer = kp * error + ki * self.integral + kd * d_error
        new_steer = np.clip(steer, -max_control, max_control)
        new_steer = alpha * new_steer + (1 - alpha) * self.prev_steer
        self.prev_steer = new_steer

        return new_steer


def main(args=None):
    
    rclpy.init(args=args)
    pure_pursuit_node = PurePursuit()
    print("PurePursuit Initialized")
    rclpy.spin(pure_pursuit_node)

    pure_pursuit_node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()

