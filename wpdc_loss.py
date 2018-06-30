#!/usr/bin/env python3
# coding: utf-8

import sys
import os.path as osp
from pathlib import Path
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import pickle
from math import sqrt
import time
from io_utils import _load, _numpy_to_cuda, _numpy_to_tensor, _load_gpu
from params import *

# d = 'configs'
# keypoints = _load(osp.join(d, 'keypoints_sim.npy'))
# w_shp = _load(osp.join(d, 'w_shp_sim.npy'))
# w_exp = _load(osp.join(d, 'w_exp_sim.npy'))  # simplified version
#
# meta = _load(osp.join(d, 'param_whitening.pkl'))
# param_mean = meta.get('param_mean')
# param_std = meta.get('param_std')
# u_shp = _load(osp.join(d, 'u_shp.npy'))
# u_exp = _load(osp.join(d, 'u_exp.npy'))
# u = u_shp + u_exp
# w = np.concatenate((w_shp, w_exp), axis=1)
# w_base = w[keypoints]
# w_norm = np.linalg.norm(w, axis=0)
# w_base_norm = np.linalg.norm(w_base, axis=0)

_to_tensor = _numpy_to_cuda  # gpu


def _parse_param_batch(param):
    """Work for both numpy and tensor"""
    N = param.shape[0]
    p_ = param[:, :12].view(N, 3, -1)
    p = p_[:, :, :3]
    offset = p_[:, :, -1].view(N, 3, 1)
    alpha_shp = param[:, 12:52].view(N, -1, 1)
    alpha_exp = param[:, 52:].view(N, -1, 1)
    return p, offset, alpha_shp, alpha_exp


class WPDCLoss(nn.Module):
    """Input and target are all 62-d param"""

    def __init__(self, opt_style='resample', resample_num=132):
        super(WPDCLoss, self).__init__()
        self.opt_style = opt_style
        self.param_mean = _to_tensor(param_mean)
        self.param_std = _to_tensor(param_std)

        self.u = _to_tensor(u)
        self.w_shp = _to_tensor(w_shp)
        self.w_exp = _to_tensor(w_exp)
        self.w_norm = _to_tensor(w_norm)

        self.w_shp_length = self.w_shp.shape[0] // 3
        self.keypoints = _to_tensor(keypoints)
        self.resample_num = resample_num

    def reconstruct_and_parse(self, input, target):
        # reconstruct
        param = input * self.param_std + self.param_mean
        param_gt = target * self.param_std + self.param_mean

        # parse param
        p, offset, alpha_shp, alpha_exp = _parse_param_batch(param)
        pg, offsetg, alpha_shpg, alpha_expg = _parse_param_batch(param_gt)

        return (p, offset, alpha_shp, alpha_exp), (pg, offsetg, alpha_shpg, alpha_expg)

    def _calc_weights_resample(self, input_, target_):
        # resample index
        if self.resample_num <= 0:
            keypoints_mix = self.keypoints
        else:
            index = torch.randperm(self.w_shp_length)[:self.resample_num].reshape(-1, 1)
            keypoints_resample = torch.cat((3 * index, 3 * index + 1, 3 * index + 2), dim=1).view(-1).cuda()
            keypoints_mix = torch.cat((self.keypoints, keypoints_resample))
        w_shp_base = self.w_shp[keypoints_mix]
        u_base = self.u[keypoints_mix]
        w_exp_base = self.w_exp[keypoints_mix]

        input = torch.tensor(input_.data.clone(), requires_grad=False)
        target = torch.tensor(target_.data.clone(), requires_grad=False)

        (p, offset, alpha_shp, alpha_exp), (pg, offsetg, alpha_shpg, alpha_expg) \
            = self.reconstruct_and_parse(input, target)

        input = self.param_std * input + self.param_mean
        target = self.param_std * target + self.param_mean

        N = input.shape[0]

        offset[:, -1] = offsetg[:, -1]

        weights = torch.zeros_like(input, dtype=torch.float)
        tmpv = (u_base + w_shp_base @ alpha_shp + w_exp_base @ alpha_exp).view(N, -1, 3).permute(0, 2, 1)

        tmpv_norm = torch.norm(tmpv, dim=2)
        offset_norm = sqrt(w_shp_base.shape[0] // 3)

        # for pose
        param_diff_pose = torch.abs(input[:, :11] - target[:, :11])
        for ind in range(11):
            if ind in [0, 4, 8]:
                weights[:, ind] = param_diff_pose[:, ind] * tmpv_norm[:, 0]
            elif ind in [1, 5, 9]:
                weights[:, ind] = param_diff_pose[:, ind] * tmpv_norm[:, 1]
            elif ind in [2, 6, 10]:
                weights[:, ind] = param_diff_pose[:, ind] * tmpv_norm[:, 2]
            else:
                weights[:, ind] = param_diff_pose[:, ind] * offset_norm

        ## This is the optimizest version
        # for shape_exp
        magic_number = 0.00057339936  # scale
        param_diff_shape_exp = torch.abs(input[:, 12:] - target[:, 12:])
        # weights[:, 12:] = magic_number * param_diff_shape_exp * self.w_norm
        w = torch.cat((w_shp_base, w_exp_base), dim=1)
        w_norm = torch.norm(w, dim=0)
        # print('here')
        weights[:, 12:] = magic_number * param_diff_shape_exp * w_norm

        eps = 1e-6
        weights[:, :11] += eps
        weights[:, 12:] += eps

        # normalize the weights
        maxes, _ = weights.max(dim=1)
        maxes = maxes.view(-1, 1)
        weights /= maxes

        # zero the z
        weights[:, 11] = 0

        return weights

    def forward(self, input, target, weights_scale=10):
        if self.opt_style == 'resample':
            weights = self._calc_weights_resample(input, target)
            loss = weights * (input - target) ** 2
            return loss.mean()
        else:
            raise Exception(f'Unknown opt style: {self.opt_style}')


if __name__ == '__main__':
    pass
