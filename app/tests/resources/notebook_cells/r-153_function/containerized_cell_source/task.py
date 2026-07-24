import numpy as np

import argparse
import json
import os
arg_parser = argparse.ArgumentParser()


arg_parser.add_argument('--id', action='store', type=str, required=True, dest='id')



args = arg_parser.parse_args()
print(args)

id = args.id




class FoxTree:
    def __init__(self, points_array, radius, vertical_resolution, min_pts_per_cluster):
        """
        :param points_array: numpy array of shape (N, 3) containing x, y, z
        """
        self.radius = radius
        self.vertical_resolution = vertical_resolution
        self.min_pts_seeds = min_pts_per_cluster
        
        self.points_data = points_array
        self.num_pts = len(points_array)
        
        self.tree_ids = np.full(self.num_pts, -1, dtype=int)
        
        self.z_min = np.min(points_array[:, 2])
        self.z_max = np.max(points_array[:, 2])
        
        self.parsed_pt_indices = [] # List of indices
        self.next_tree_id = 0
        
        self.trees = {}

    def generate_tree_clusters(self, pt_clusters):
        for cluster_indices in pt_clusters:
            current_id = self.next_tree_id
            self.next_tree_id += 1
            self.trees[current_id] = []
            for idx in cluster_indices:
                self.tree_ids[idx] = current_id
                self.trees[current_id].append(idx)

