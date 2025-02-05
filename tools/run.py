from collections import deque
import rospy
from sensor_msgs.msg import CameraInfo, Image as ImageMsg
import message_filters
import cv2
import os
import numpy as np
import tf
from nav_msgs.msg import Odometry
from geometry_msgs.msg import Point, Pose, Quaternion, Twist, Vector3
from visualization_msgs.msg import MarkerArray, Marker
from test import *
import os,sys,inspect
currentdir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
parentdir = os.path.dirname(currentdir)
sys.path.insert(0,parentdir)
from experiments.siammask_sharp.custom import Custom
from sort import *
from Bezier import Bezier
from collections import deque
from MiDaS import MiDaS
import copy
import pyrealsense2
import threading
from pykalman import KalmanFilter

parser = argparse.ArgumentParser(description='PyTorch Tracking Demo')

parser.add_argument('--resume', default='experiments/siammask_sharp/SiamMask_DAVIS.pth', type=str,
                    metavar='PATH',help='path to latest checkpoint (default: none)')
parser.add_argument('--config', dest='config', default='experiments/siammask_sharp/config_davis.json',
                    help='hyper-parameter of SiamMask in json format')
parser.add_argument('--base_path', default='../../data/tennis', help='datasets')
parser.add_argument('--cpu', action='store_true', help='cpu mode')
args = parser.parse_args()
sampling_rate = 30
markerarray_i=0

class Nodo(object):
    def __init__(self):
        # Params
        self.image = None
        self.undist_image = None
        self.depth = None
        self.camera_info_K = None
        self.camera_info_D = None

        # Node cycle rate (in Hz).
        self.loop_rate = rospy.Rate(sampling_rate)
        self.moving_object_odom_rate = rospy.Rate(sampling_rate)

        # Subscribers
        rgb_sub = message_filters.Subscriber("/r200/rgb/image_raw",ImageMsg)
        depth_sub = message_filters.Subscriber("/r200/depth/image_raw",ImageMsg)
        camera_info_msg = rospy.wait_for_message("/r200/rgb/camera_info",CameraInfo)#, self.info_callback)
        self.intrinsics = pyrealsense2.intrinsics()
        self.intrinsics.width = camera_info_msg.width
        self.intrinsics.height = camera_info_msg.height
        self.intrinsics.ppx = camera_info_msg.K[2]
        self.intrinsics.ppy = camera_info_msg.K[5]
        self.intrinsics.fx = camera_info_msg.K[0]
        self.intrinsics.fy = camera_info_msg.K[4]
        self.intrinsics.model = pyrealsense2.distortion.brown_conrady
        self.intrinsics.coeffs = [i for i in camera_info_msg.D]
        self.camera_info_K = np.array(camera_info_msg.K).reshape([3, 3])
        self.camera_info_D = np.array(camera_info_msg.D)
        ts = message_filters.TimeSynchronizer([rgb_sub, depth_sub], 60)#, 0.2,allow_headerless=False)
        ts.registerCallback(self.callback)

    def callback(self, rgb_msg, depth_msg):
        self.image = np.frombuffer(rgb_msg.data, dtype=np.uint8).reshape(rgb_msg.height, rgb_msg.width, -1)
        self.undist_image = cv2.undistort(self.image, self.camera_info_K, self.camera_info_D)
        self.depth = np.frombuffer(depth_msg.data, dtype=np.float32).reshape(depth_msg.height, depth_msg.width, -1)
    
    def frame_capture(self):
        self.loop_rate.sleep()
        return self.image

    def undist_frame_capture(self):
        self.loop_rate.sleep()
        return self.undist_image

    def depth_frame_capture(self):
        self.loop_rate.sleep()
        return self.depth

class KalmanFilterCV:
    kf = cv2.KalmanFilter(4, 2)
    kf.measurementMatrix = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], np.float32)
    kf.transitionMatrix = np.array([[1, 0, 1, 0], [0, 1, 0, 1], [0, 0, 1, 0], [0, 0, 0, 1]], np.float32)


    def predict(self, coordX, coordY):
        ''' This function estimates the position of the object'''
        measured = np.array([[np.float32(coordX)], [np.float32(coordY)]])
        self.kf.correct(measured)
        predicted = self.kf.predict()
        x, y = int(predicted[0]), int(predicted[1])
        return x, y

class KalmanFilterEstimator:
    # Parameters Initialization
    def __init__(self, sensor_mean, sensor_variance, sensor_reading) -> None:
        self.mu_xi, self.var_xi = 0.0, 6400 # mean and variance of the model
        self.mu_eta, self.var_eta = sensor_mean, sensor_variance # mean and variance of the sensor
        self.x_opt = 0.0  # optimal filtered values
        self.e = 0.0 # mean of the square errors
        self.K = 0.0 # Kalman coefficient's value over time

        # base of the iteration
        self.x_opt = 0.0
        self.e = self.var_eta
        self.base_iteration = True
       
    def step(self, sensor_reading):
        z = sensor_reading # sensor readings
        if self.base_iteration:
            self.base_iteration = False
            self.x_opt = z
        self.e = self.var_eta*(self.e+self.var_xi)/(self.e+self.var_xi+self.var_eta)
        self.K = self.e/self.var_eta
        self.x_opt = self.K*z + (1-self.K)*(self.x_opt)
        return self.x_opt

class KalmanFilterPy:
    def __init__(self, pos) -> None:
        self.initial_state_mean = [pos[0], 0, pos[1], 0, pos[2], 0]
        self.observation_matrix = [[1, 0, 0, 0, 0, 0],
                                   [0, 0, 1, 0, 0, 0],
                                   [0, 0, 0, 0, 1, 0],]
        self.transition_matrix = [[1, 1, 0, 0, 0, 0],
                                  [0, 1, 0, 0, 0, 0],
                                  [0, 0, 1, 1, 0, 0],
                                  [0, 0, 0, 1, 0, 0],
                                  [0, 0, 0, 0, 1, 1],
                                  [0, 0, 0, 0, 0, 1]]
        self.observation_matrix = [[1, 0, 0, 0, 0, 0],
                                   [0, 0, 1, 0, 0, 0],
                                   [0, 0, 0, 0, 1, 0],]
        self.process_noise_cov = [[1, 0, 0, 0],
                                  [0, 1, 0, 0],
                                  [0, 0, 1, 0],
                                  [0, 0, 0, 1]]
        self.observation_noise_cov = [[1, 0, 0, 0],
                                      [0, 1, 0, 0],
                                      [0, 0, 1, 0]]
        self.kf = KalmanFilter(n_dim_state=6,
                                n_dim_obs=3,
                                transition_matrices = self.transition_matrix,
                                observation_matrices = self.observation_matrix,
                                initial_state_mean = self.initial_state_mean,
                                random_state=np.random.RandomState(0)
                                )

    def step(self, current_pos, current_cov, measurement):
        new_pos, new_cov = \
                    self.kf.filter_update(current_pos, current_cov, measurement)
        return new_pos, new_cov

def convert_2D_to_3D_coords(x_image, y_image, x0, y0, fx, fy, z_3D):
    """
    you can find the values of the camera intrinsic parameters at ./data/depth_Depth_metadata.csv
    """
    camera_principle_point_x = x0
    camera_principle_point_y = y0
    camera_focal_length_x = fx
    camera_focal_legnth_y = fy

    # Formuals to calculate the x and y in 3D (As we studied Pinhole camera model in the lab )
    x_3D = (x_image - camera_principle_point_x) * z_3D / camera_focal_length_x
    y_3D = (y_image - camera_principle_point_y) * z_3D / camera_focal_legnth_y
    
    return x_3D, y_3D, z_3D

def convert_2d_to_3d_using_realsense(x, y, depth, intrinsics):
    result = pyrealsense2.rs2_deproject_pixel_to_point(intrinsics, [x, y], depth)
    x_3D, y_3D, z_3D = result[2], result[0], result[1]
    return x_3D, y_3D, z_3D

class OdomMapper:
    def __init__(self) -> None:
        self.drone_odom_position = None
        self.drone_odom_orientation = None
    
    def drone_odom_callback(self, drone_odom):
        # global drone_odom_position, drone_odom_orientation
        self.drone_odom_position = drone_odom.pose.pose.position
        self.drone_odom_orientation = drone_odom.pose.pose.orientation

def odom_publisher(mapper, odom, odom_pub, curve_position_set, curve_velocity_set):
    r = rospy.Rate(sampling_rate)
    for pos, vel in zip(curve_position_set, curve_velocity_set):
        # set the position
        pos_x = mapper.drone_odom_position.x + pos[0]
        pos_y = mapper.drone_odom_position.y + pos[1]
        pos_z = mapper.drone_odom_position.z + pos[2]
        odom.pose.pose = Pose(Point(*(pos_x, pos_y, pos_z)), mapper.drone_odom_orientation)
        # set the velocity
        odom.child_frame_id = "moving_object_odom"
        odom.twist.twist = Twist(Vector3(*vel), Vector3(0, 0, 0))
        # publish the message
        odom_pub.publish(odom)
        r.sleep()

def markerarray_publisher(mapper, marker, marker_array_msg, markerarray_pub, curve_position_set, curve_velocity_set):
    global markerarray_i
    for pos in curve_position_set:
        # set the position
        pos_x = mapper.drone_odom_position.x + pos[0]
        pos_y = mapper.drone_odom_position.y + pos[1]
        pos_z = mapper.drone_odom_position.z + pos[2]
        marker.header.frame_id = "/map"
        marker.id = markerarray_i
        marker.type = marker.SPHERE
        marker.action = marker.ADD
        marker.color.r = 0.2
        marker.color.g = 0.2
        marker.color.b = 0.0
        marker.color.a = 1.0
        marker.scale.x = 0.2
        marker.scale.y = 0.2
        marker.scale.z = 0.2
        marker.pose.position.x = pos_x
        marker.pose.position.y = pos_y
        marker.pose.position.z = pos_z
        marker.pose.orientation.w = 1.0        
        marker_array_msg.markers.append(marker)
    # publish the message
    markerarray_pub.publish(marker_array_msg)
    markerarray_i += 1
    # r.sleep()

def stay_still_odom_publisher(mapper, odom, odom_pub):
    r = rospy.Rate(sampling_rate)
    odom.pose.pose = Pose(mapper.drone_odom_position, mapper.drone_odom_orientation)
    odom.child_frame_id = "moving_object_odom"
    odom.twist.twist = Twist(Vector3(*(0, 0, 0)), Vector3(0, 0, 0))

    # publish the message
    for _ in range(100):
        odom_pub.publish(odom)
        r.sleep()
        
if __name__ == '__main__':
     # Setup device
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    torch.backends.cudnn.benchmark = True
    # create instance of Odom mapper
    mapper = OdomMapper()
    #create instance of SORT
    sort_tracker = None#Sort()
    # Load Kalman filter to predict the trajectory
    kf = KalmanFilterCV()
    # MiDaS
    midas = MiDaS()
    # Setup Model
    cfg = load_config(args)
    siammask = Custom(anchors=cfg['anchors'])
    if args.resume:
        assert isfile(args.resume), 'Please download {} first.'.format(args.resume)
        siammask = load_pretrain(siammask, args.resume)

    siammask.eval().to(device)
    rospy.init_node("siammaskimage", anonymous=True)
    rospy.Subscriber("/mavros/local_position/odom", Odometry, mapper.drone_odom_callback)
    odom_pub = rospy.Publisher("moving_object_odom", Odometry, queue_size=50)
    markerarray_pub = rospy.Publisher("moving_object_markerarray", MarkerArray, queue_size=50)
    odom_broadcaster = tf.TransformBroadcaster()
    markarray_broadcaster = tf.TransformBroadcaster()
    rate = rospy.Rate(sampling_rate)
    my_node = Nodo()
    original_frame = None
    while original_frame is None:
        _ = my_node.frame_capture()
        original_frame = my_node.undist_frame_capture()
        depth_frame = my_node.depth_frame_capture()
        print("frame still loading")
    relative_depth_frame_colored = midas.estimate(original_frame)
    frame = copy.deepcopy(original_frame)
    # Select ROI
    cv2.namedWindow("Demo", cv2.WND_PROP_FULLSCREEN)
    try:
        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        init_rect = cv2.selectROI('Demo', frame, False, False)
        x, y, w, h = init_rect
    except Exception as e:
        print(e)
        print("Something wrong happened!")
        exit()

    toc = 0
    f = 0
    camera_focal_length_x = my_node.camera_info_K[0,0] #fx
    camera_focal_length_y = my_node.camera_info_K[1,1] #fy
    camera_principle_point_x = my_node.camera_info_K[0,2] #x0
    camera_principle_point_y = my_node.camera_info_K[1,2] #y0
    
    current_time = rospy.Time.now()
    last_time = rospy.Time.now()
    position_queue = deque(maxlen=6)
    velocity_queue = deque(maxlen=6)
    t_points = np.arange(0, 1, 0.03)
    # create an inverse from the colormap to gray values
    gray_values = np.arange(256, dtype=np.uint8)
    color_values = map(tuple, cv2.applyColorMap(gray_values, cv2.COLORMAP_HOT).reshape(256, 3))
    color_to_gray_map = dict(zip(color_values, gray_values))
    s=True
    prev_target_mask = None
    prev_target_sz = None
    periodic_target_sz = None
    periodic_flag = True
    track_flag = True
    deccelerating_rate = 1.0
    lost_counter = 0
    current_cov = [0, 0, 0, 0, 0, 0]
    while not rospy.is_shutdown():
        current_time = rospy.Time.now()
        # since all odometry is 6DOF we'll need a quaternion created from yaw
        odom_quat = tf.transformations.quaternion_from_euler(0, 0, 0)
        
        tic = cv2.getTickCount()
        # Capture the video frame
        _ = my_node.frame_capture()
        original_frame = my_node.undist_frame_capture()
        depth_frame = my_node.depth_frame_capture()
        relative_depth_frame, magma_relative_depth_map = midas.estimate(original_frame)
        frame = copy.deepcopy(original_frame)
        relative_depth_frame = relative_depth_frame[..., np.newaxis].astype(np.float)
         
        depth_masked = copy.deepcopy(depth_frame)
        depth_masked[np.isnan(depth_masked)] = 0
        depth_masked[depth_masked<1e-2] = 0
        mask_d = depth_masked.astype(np.bool)
        inv_mask = (mask_d != np.ones_like(mask_d))
        inversed_relative_depth_frame = (1. - relative_depth_frame)
        inversed_relative_depth_frame_std = inversed_relative_depth_frame.std()
        inversed_relative_depth_frame_mean = inversed_relative_depth_frame.mean()
        standarized_inversed_relative_depth_frame = (inversed_relative_depth_frame - inversed_relative_depth_frame.min())/inversed_relative_depth_frame_std
        unit_vector_inversed_relative_depth_frame = standarized_inversed_relative_depth_frame / np.linalg.norm(standarized_inversed_relative_depth_frame.squeeze(), 1)
        depth_ratio_array = depth_masked[mask_d] / (unit_vector_inversed_relative_depth_frame[mask_d]+1e-5)
        depth_ratio = depth_ratio_array[np.nonzero(depth_ratio_array)].mean()
        depth_ratio_std = depth_ratio_array[np.nonzero(depth_ratio_array)].std()
        if np.isnan(depth_ratio) or np.isnan(depth_ratio_std):
            depth_ratio = 50000
            depth_ratio_std = 50000
        relative_depth = unit_vector_inversed_relative_depth_frame*inv_mask*depth_ratio/3
        depth_hybrid = relative_depth + depth_masked #3.4
        cv2.imshow("depth", depth_frame)
        cv2.imshow("relative_depth", magma_relative_depth_map)
        cv2.imshow("test", (255*relative_depth_frame).astype(np.uint8))
        mxx = np.max(depth_hybrid.squeeze())
        cv2.imshow("depth_hybrid", (255/mxx*depth_hybrid).astype(np.uint8))
        if f == 0:  # init
            f = 1
            target_pos = np.array([x + w / 2, y + h / 2])
            target_sz = np.array([w, h])
            x_image = int(target_pos[0])
            y_image = int(target_pos[1])
            x_3D = int(np.sum(depth_hybrid[y_image-3:y_image+3, x_image-3:x_image+3]))/36
            vx_3D = 0
            kf_estimator = KalmanFilterPy([x_3D, x_image, y_image])
            target_depth = x_3D
            state = siamese_init(original_frame, target_pos, target_sz, target_depth, siammask, cfg['hp'], device=device)  # init tracker
            print(depth_hybrid)
            print("relative depth")
            print(relative_depth_frame)
            periodic_target_sz = prev_target_sz = state['target_sz']
        elif f > 0:  # tracking
            state = siamese_track(state, original_frame, depth_hybrid, siammask, cfg, sort_tracker=sort_tracker, mask_enable=True, refine_enable=True, reset_template=True, device=device)  # track
            
            if f == 1:
                track_flag = True
            elif state['pred_cls'] != state['init_pred_cls']:#f==1 or (state['score'] >= 0.7 and (x_3D_old>2 or state['pred_cls']==0)): # and true_pos_object and periodic_flag):                
                lost_counter += 1
                if lost_counter >= 5:
                    track_flag = False
            else:
                lost_counter = 0
                track_flag = True
            if track_flag:
                if f == 1:
                    x_3D_old = x_3D
                location = state['ploygon'].flatten()
                mask = state['mask'] > state['p'].seg_thr
                predicted = kf.predict(state['target_pos'][0], state['target_pos'][1])
                #cv2.rectangle(frame, (x, y), (x2, y2), (255, 0, 0), 4)
                if state['pred_cls']==state['init_pred_cls']:
                    cv2.circle(frame, (int(state['target_pos'][0]), int(state['target_pos'][1])), 20, (0, 0, 255), 4)
                x_image = int(state['target_pos'][0])
                y_image = int(state['target_pos'][1])
                x_3D = np.sum(depth_hybrid[y_image-2:y_image+2, x_image-2:x_image+2])/16

                x_3D = round(x_3D, 3)
                x_3D, y_3D, z_3D = convert_2d_to_3d_using_realsense(x, y, x_3D, my_node.intrinsics)
                y_3D /= 10
                z_3D /= -10
                
                if f>1:
                    [x_3D, vx_3D, y_3D, vy_3D, z_3D, vz_3D], current_cov = kf_estimator.step([x_3D_old, vx_3D, y_3D_old, vy_3D, z_3D_old, vz_3D],\
                                                                        current_cov, [x_3D, y_3D, z_3D])
                y_3D = round(y_3D, 3)
                z_3D = round(z_3D, 3)
                if f==1:
                    x_3D_old, y_3D_old, z_3D_old = x_3D, y_3D, z_3D
                vx_3D, vy_3D, vz_3D = x_3D - x_3D_old, y_3D - y_3D_old, z_3D - z_3D_old
                if x_3D <= 2:
                    deccelerating_rate = 0
                elif x_3D <= 7:
                    deccelerating_rate -= (deccelerating_rate*0.2)
                else:
                    deccelerating_rate = 1.0
                x_3D, y_3D, z_3D = x_3D*deccelerating_rate, y_3D, z_3D #, 0, 0, 0    
                position_queue.append([x_3D, y_3D, z_3D])
                velocity_queue.append([vx_3D, vy_3D, vz_3D])
                print("X-target = {}, Y-target = {}, Z-target = {}".format(x_3D, y_3D, z_3D))
                
                # first, we'll publish the transform over tf
                odom_broadcaster.sendTransform(
                    (x_3D, y_3D, z_3D),
                    odom_quat,
                    current_time,
                    "moving_object_odom",
                    "base_link"
                    
                )
                
                # next, we'll publish the odometry message over ROS
                odom = Odometry()
                odom.header.stamp = current_time
                odom.header.frame_id = "moving_object_odom"
                curve_position_set = Bezier.Curve(t_points, np.array(position_queue))
                curve_velocity_set = Bezier.Curve(t_points, np.array(velocity_queue))
                #publish bezier curves of position and velocity
                thread = threading.Thread(target=odom_publisher, kwargs={'mapper':mapper, 'odom': odom, 'odom_pub':odom_pub,
                                                                         'curve_position_set':curve_position_set,
                                                                         'curve_velocity_set':curve_velocity_set})
                thread.start()
                
                # let's publish MarkerArray of the Moving Object Pose
                marker_array_msg = MarkerArray()
                marker = Marker()
                thread2 = threading.Thread(target=markerarray_publisher, kwargs={'mapper':mapper, 'marker': marker,
                                                                                'marker_array_msg': marker_array_msg,
                                                                                'markerarray_pub':markerarray_pub,
                                                                                'curve_position_set':curve_position_set,
                                                                                'curve_velocity_set':curve_velocity_set})
                thread2.start()
                
                last_time = current_time
                x_3D_old, y_3D_old, z_3D_old = x_3D, y_3D, z_3D
                
                if state['pred_cls']==state['init_pred_cls']:
                    frame[:, :, 2] = (mask > 0) * 255 + (mask == 0) * frame[:, :, 2]
                    cv2.polylines(frame, [np.int0(location).reshape((-1, 1, 2))], True, (0, 255, 0), 3)
                    frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                    cv2.imshow('Demo', frame)
                key = cv2.waitKey(1)
                if key > 0:
                    break
            else:
                print("OBJECT is LOST!!")
                x_3D_old = 0
                x_3D, y_3D, z_3D = x_3D_old, y_3D_old, z_3D_old
                vx_3D, vy_3D, vz_3D = 0, 0, 0
                # first, we'll publish the transform over tf
                odom_broadcaster.sendTransform(
                    (x_3D, y_3D, z_3D),
                    odom_quat,
                    current_time,
                    "moving_object_odom",
                    "base_link"  
                )
                
                # next, we'll publish the odometry message over ROS
                odom = Odometry()
                odom.header.stamp = current_time
                odom.header.frame_id = "moving_object_odom"

                # publish the message
                thread = threading.Thread(target=stay_still_odom_publisher, kwargs={'mapper':mapper, 'odom':odom, 'odom_pub':odom_pub})
                thread.start()
                # let's publish MarkerArray of the Moving Object Pose
                marker_array_msg = MarkerArray()
                marker = Marker()
                thread2 = threading.Thread(target=markerarray_publisher, kwargs={'mapper':mapper, 'marker': marker,
                                                                                'marker_array_msg': marker_array_msg,
                                                                                'markerarray_pub':markerarray_pub,
                                                                                'curve_position_set':curve_position_set,
                                                                                'curve_velocity_set':curve_velocity_set})
                thread2.start()
                
                last_time = current_time
                frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                cv2.imshow('Demo', frame)
                key = cv2.waitKey(1)
                if key > 0:
                    break
                f+=1
                key = cv2.waitKey(1)
                if key > 0:
                    rospy.spin()
                    break
           
            f+=1
        toc += cv2.getTickCount() - tic
    toc /= cv2.getTickFrequency()
    fps = f / toc
    print('Time: {:02.1f}s Speed: {:3.1f}fps (with visulization!)'.format(toc, fps))
    
    