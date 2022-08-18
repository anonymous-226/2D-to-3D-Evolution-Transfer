# -----------------------------------------------------------
# Copyright (C) 2020 NVIDIA Corporation. All rights reserved.
# Nvidia Source Code License-NC
# Code written by Xueting Li.
# -----------------------------------------------------------
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import pdb
import copy
import math
import numpy as np
import os.path as osp
from absl import app, flags

import torch
import torchvision
import torch.nn as nn

from ..utils import mesh

from . import geom_utils
from . import net_blocks as nb


#------------- Modules ------------#
#----------------------------------#
class ResNetConv(nn.Module):
    def __init__(self, n_blocks=4):
        super(ResNetConv, self).__init__()
        self.resnet = torchvision.models.resnet18(pretrained=True)
        self.n_blocks = n_blocks

    def forward(self, x):
        n_blocks = self.n_blocks
        x = self.resnet.conv1(x)
        x = self.resnet.bn1(x)
        x = self.resnet.relu(x)
        x = self.resnet.maxpool(x)

        if n_blocks >= 1:
            x = self.resnet.layer1(x)
        if n_blocks >= 2:
            x = self.resnet.layer2(x)
        if n_blocks >= 3:
            x = self.resnet.layer3(x)
        if n_blocks >= 4:
            x = self.resnet.layer4(x)
        return x


class Encoder(nn.Module):
    """
    Current:
    Resnet with 4 blocks (x32 spatial dim reduction)
    Another conv with stride 2 (x64)
    This is sent to 2 fc layers with final output nz_feat.
    """

    def __init__(self, input_shape, n_blocks=4, nz_feat=100, batch_norm=True, z_dim=200):
        super(Encoder, self).__init__()
        self.resnet_conv = ResNetConv(n_blocks=4)
        self.enc_conv1 = nb.conv2d(batch_norm, 512, 256, stride=2, kernel_size=4)
        nc_input = 256 * (input_shape[0] // 64) * (input_shape[1] // 64)
        self.enc_fc = nb.fc_stack(nc_input, nz_feat, 2)

        self.mean_fc = nn.Sequential(nn.Linear(nz_feat, nz_feat),
                                     nn.LeakyReLU(),
                                     nn.Linear(nz_feat, z_dim))

        self.logvar_fc = nn.Sequential(nn.Linear(nz_feat, nz_feat),
                                       nn.LeakyReLU(),
                                       nn.Linear(nz_feat, z_dim))

        nb.net_init(self.enc_conv1)



    def sampling(self, mu, logvar):
        # logvar= -12.25, -13.65, -14.20, -12.96
        var = logvar.mul(0.5).exp_()# 0.00, 0.00, 0.00, 0.00, 0.00, 0.00
        eps = torch.FloatTensor(var.size()).normal_()
        eps = eps.cuda()
        return eps.mul(var).add_(mu)#equals eps

    def forward(self, img):
        resnet_feat = self.resnet_conv(img)

        out_enc_conv1 = self.enc_conv1(resnet_feat)
        out_enc_conv1 = out_enc_conv1.view(img.size(0), -1)
        img_feat = self.enc_fc(out_enc_conv1)

        mean = self.mean_fc(img_feat)
        logvar = self.logvar_fc(img_feat)

        return img_feat, self.sampling(mean, logvar), mean, logvar

class TexturePredictorUV(nn.Module):
    """
    Outputs mesh texture
    """

    def __init__(self, nz_feat, F, T, opts, img_H=64, img_W=128, n_upconv=5, nc_init=256, predict_flow=False, symmetric=False, num_sym_faces=624):
        super(TexturePredictorUV, self).__init__()
        self.feat_H = img_H // (2 ** n_upconv)
        self.feat_W = img_W // (2 ** n_upconv)
        self.nc_init = nc_init
        self.symmetric = symmetric
        self.num_sym_faces = num_sym_faces
        self.F = F
        self.T = T
        self.predict_flow = predict_flow

        self.enc = nb.fc_stack(nz_feat, self.nc_init*self.feat_H*self.feat_W, 2)
        if predict_flow:
            nc_final=2
        else:
            nc_final=3
        self.decoder = nb.decoder2d(n_upconv, None, nc_init, init_fc=False, nc_final=nc_final, use_deconv=opts.use_deconv, upconv_mode=opts.upconv_mode)

    def forward(self, feat, uv_sampler):
        bs = feat.size(0)
        uvimage_pred = self.enc(feat)
        uvimage_pred = uvimage_pred.view(uvimage_pred.size(0), self.nc_init, self.feat_H, self.feat_W)
        # B x 2 or 3 x H x W
        self.uvimage_pred = self.decoder(uvimage_pred)

        if torch.sum(self.uvimage_pred != self.uvimage_pred) > 0:
            print('Texture branch got Nan!!')
            pdb.set_trace()

        self.uvimage_pred = torch.tanh(self.uvimage_pred)
        tex_pred = torch.nn.functional.grid_sample(self.uvimage_pred, uv_sampler,align_corners=True)
        # print(tex_pred.shape, self.uvimage_pred.shape, uv_sampler.shape)  # [8, 2, 656, 36] [8, 2, 128, 256] [8, 656, 36, 2]
        tex_pred = tex_pred.view(tex_pred.size(0), -1, self.F, self.T, self.T).permute(0, 2, 3, 4, 1)
        # print(tex_pred.view(tex_pred.size(0), -1, self.F, self.T, self.T).shape)


        if self.symmetric:
            # Symmetrize.
            tex_left = tex_pred[:, -self.num_sym_faces:]
            return torch.cat([tex_pred, tex_left], 1), self.uvimage_pred
        else:
            # Contiguous Needed after the permute..
            return tex_pred.contiguous(), self.uvimage_pred



class ShapePredictor(nn.Module):
    """
    Outputs mesh deformations
    """

    def __init__(self, nz_feat, num_verts):
        super(ShapePredictor, self).__init__()
        self.pred_layer = nn.Linear(nz_feat, num_verts * 3)
        self.pred_layer.weight.data.normal_(0, 0.0001)

    def forward(self, feat):
        delta_v = self.pred_layer(feat)
        # Make it B x num_verts x 3
        delta_v = delta_v.view(delta_v.size(0), -1, 3)

        return delta_v


class QuatPredictor(nn.Module):
    def __init__(self, nz_feat, nz_rot=4, classify_rot=False):
        super(QuatPredictor, self).__init__()
        self.pred_layer = nn.Linear(nz_feat, nz_rot)
        self.classify_rot = classify_rot

    def forward(self, feat):
        quat = self.pred_layer(feat)
        if self.classify_rot:
            quat = torch.nn.functional.log_softmax(quat)
        else:
            quat = torch.nn.functional.normalize(quat)
        return quat

    def initialize_to_zero_rotation(self,):
        nb.net_init(self.pred_layer)
        self.pred_layer.bias = nn.Parameter(torch.FloatTensor([1,0,0,0]).type(self.pred_layer.bias.data.type()))
        return

class ScalePredictor(nn.Module):

    def __init__(self, nz, bias=1.0, lr=1.0):
        super(ScalePredictor, self).__init__()
        self.pred_layer = nn.Linear(nz, 1)
        self.lr = lr
        self.bias = bias

    def forward(self, feat):
        scale = self.lr * self.pred_layer.forward(feat) + self.bias # b
        scale = torch.nn.functional.relu(scale) + 1E-12  # minimum scale is 0.0
        return scale

class TransPredictor(nn.Module):
    """
    Outputs [tx, ty] or [tx, ty, tz]
    """

    def __init__(self, nz, orth=True):
        super(TransPredictor, self).__init__()
        if orth:
            self.pred_layer = nn.Linear(nz, 2)
        else:
            self.pred_layer = nn.Linear(nz, 3)

    def forward(self, feat):
        trans = self.pred_layer(feat)
        # print('trans: ( Mean = {}, Var = {} )'.format(trans.mean().data[0], trans.var().data[0]))
        return trans

class QuatPredictorAzEle(nn.Module):

    def __init__(self, nz_feat, dataset='others'):
        super(QuatPredictorAzEle, self).__init__()
        self.pred_layer = nn.Linear(nz_feat, 3)
        self.register_buffer('axis', torch.eye(3).float())
        self.dataset = dataset

    def forward(self, feat):
        angles = 0.1*self.pred_layer.forward(feat)
        angles = torch.tanh(feat)
        azimuth = math.pi/6 * angles[...,0]

        # # Birds
        if self.dataset == 'cub':
            elev = math.pi/2 * (angles[...,1])
            cyc_rot = math.pi/3 * (angles[...,2])
        else:
            # cars # Horse & Sheep
            elev = math.pi/9 * (angles[...,1])
            cyc_rot = math.pi/9 * (angles[...,2])

        q_az = self.convert_ax_angle_to_quat(self.axis[1], azimuth)
        q_el = self.convert_ax_angle_to_quat(self.axis[0], elev)
        q_cr = self.convert_ax_angle_to_quat(self.axis[2], cyc_rot)
        quat = geom_utils.hamilton_product(q_el.unsqueeze(1), q_az.unsqueeze(1))
        quat = geom_utils.hamilton_product(q_cr.unsqueeze(1), quat)
        return quat.squeeze(1)

    def convert_ax_angle_to_quat(self, ax, ang):
        qw = torch.cos(ang/2)
        qx = ax[0] * torch.sin(ang/2)
        qy = ax[1] * torch.sin(ang/2)
        qz = ax[2] * torch.sin(ang/2)
        quat = torch.stack([qw, qx, qy, qz], dim=1)
        return quat

    def initialize_to_zero_rotation(self,):
        nb.net_init(self.pred_layer)
        return

class Camera(nn.Module):

    def __init__(self, nz_input, az_ele_quat=False, scale_lr=0.05, scale_bias=1.0, dataset='others'):
        super(Camera, self).__init__()
        self.fc_layer = nb.fc_stack(nz_input, nz_input, 2)

        if az_ele_quat:
            self.quat_predictor = QuatPredictorAzEle(nz_input, dataset)
        else:
            self.quat_predictor = QuatPredictor(nz_input)

        self.prob_predictor = nn.Linear(nz_input, 1)
        self.scale_predictor = ScalePredictor(nz_input)
        self.trans_predictor = TransPredictor(nz_input)

    def forward(self, feat):
        feat = self.fc_layer(feat)
        quat_pred = self.quat_predictor.forward(feat)
        prob = self.prob_predictor(feat)
        scale = self.scale_predictor.forward(feat)
        trans = self.trans_predictor.forward(feat)
        return torch.cat([quat_pred, prob, scale, trans], dim=1)

    def init_quat_module(self,):
        self.quat_predictor.initialize_to_zero_rotation()

class MultiCamPredictor(nn.Module):

    def __init__(self, nc_input, ns_input, nz_channels, nz_feat=100, num_cams=8,
                 aze_ele_quat=False, scale_lr=0.05, scale_bias=1.0, dataset='others'):
        super(MultiCamPredictor, self).__init__()

        self.fc = nb.fc_stack(nz_feat, nz_feat, 2, use_bn=False)
        self.scale_predictor = ScalePredictor(nz_feat)
        nb.net_init(self.scale_predictor)
        self.trans_predictor = TransPredictor(nz_feat)
        nb.net_init(self.trans_predictor)
        self.prob_predictor = nn.Linear(nz_feat, num_cams)
        self.camera_predictor = nn.ModuleList([Camera(nz_feat,aze_ele_quat, scale_lr=scale_lr,
                                                      scale_bias=scale_bias, dataset=dataset) for i in range(num_cams)])

        nb.net_init(self)
        for cx in range(num_cams):
            self.camera_predictor[cx].init_quat_module()

        self.quat_predictor = QuatPredictor(nz_feat)
        self.quat_predictor.initialize_to_zero_rotation()
        self.num_cams = num_cams

        base_rotation = torch.FloatTensor([0.9239, 0, 0.3827 , 0]).unsqueeze(0).unsqueeze(0) ##pi/4
        base_bias = torch.FloatTensor([ 0.7071,  0.7071,   0,   0]).unsqueeze(0).unsqueeze(0)
        cam_biases = [base_bias]
        for i in range(1,self.num_cams):
            cam_biases.append(geom_utils.hamilton_product(base_rotation, cam_biases[i-1]))
        cam_biases = torch.stack(cam_biases).squeeze()
        self.register_buffer("cam_biases", cam_biases)
        return

    def forward(self, feat):
        feat = self.fc(feat)
        cameras = []
        for cx in range(self.num_cams):
            cameras.append(self.camera_predictor[cx].forward(feat))
        cameras = torch.stack(cameras, dim=1)
        quats = cameras[:, :, 0:4]
        prob_logits = cameras[:, :, 4]
        camera_probs = nn.functional.softmax(prob_logits, dim=1)

        scale = self.scale_predictor.forward(feat).unsqueeze(1).repeat(1, self.num_cams, 1)
        trans = self.trans_predictor.forward(feat).unsqueeze(1).repeat(1, self.num_cams, 1)
        scale = cameras[:,:,5:6]
        trans = cameras[:,:,6:8]

        new_quats = quats
        cam = torch.cat([scale, trans, new_quats, camera_probs.unsqueeze(-1)], dim=2)
        return self.sample(cam) + (quats,)

    def sample(self, cam):
        '''
            cams : B x num_cams x 8 Vector. Last column is probs.
            cam: [batch size, number of cameras, camera parameters][16, 8, 8]
            sampled_cam: sampled camera
            sample_inds: sampled index
        '''
        dist = torch.distributions.multinomial.Multinomial(probs=cam[:, :, 7])
        sample = dist.sample()
        sample_inds = torch.nonzero(sample)[:, None, 1]
        sampled_cam = torch.gather(cam, dim=1, index=sample_inds.unsqueeze(-1).repeat(1, 1, 8)).squeeze()[:, 0:7]
        #torch.Size([16, 7]) torch.Size([16, 1]) torch.Size([16, 8]) torch.Size([16, 8, 7])
        return sampled_cam, sample_inds, cam[:, :, 7], cam[:, :, 0:7]

#------------ Mesh Net ------------#
#----------------------------------#
class MeshNet(nn.Module):
    def __init__(self, input_shape, opts, nz_feat = 100, axis = 0, temp_path = None):
        # Input shape is H x W of the image.
        super(MeshNet, self).__init__()
        self.opts = opts
        self.pred_texture = opts.use_texture
        self.symmetric = opts.symmetric
        self.symmetric_texture = opts.symmetric_texture
        self.pred_cam = opts.pred_cam
        self.nz_feat = nz_feat
        self.z_dim = opts.z_dim
        self.batch_size = opts.batch_size

        verts, faces = mesh.create_sphere(opts.subdivide)
        num_verts = verts.shape[0]

        if self.symmetric:
            verts, faces, num_indept, num_sym, num_indept_faces, num_sym_faces = mesh.make_symmetric(verts, faces, axis=axis)

            num_sym_output = num_indept + num_sym
            self.num_output = num_sym_output
            self.num_sym = num_sym
            self.num_indept = num_indept
            self.num_indept_faces = num_indept_faces
            self.num_sym_faces = num_sym_faces

            # mean shape is only half.
            mean_v = nn.Parameter(torch.Tensor(verts[:num_sym_output]).cuda())
            if(temp_path is not None):
                mean_v = torch.load(osp.join(temp_path, "mean_v.pth"))
            self.register_buffer('mean_v', mean_v)

            # Needed for symmetrizing..
            self.flip = torch.ones(1, 3).cuda()
            self.flip[0, axis] = -1
        else:
            self.mean_v = nn.Parameter(torch.Tensor(verts), requires_grad=False)
            self.num_output = num_verts

        verts_np = verts
        faces_np = faces
        self.verts_np = verts_np
        self.faces_np = faces_np
        self.faces = torch.LongTensor(faces).cuda()

        self.encoder = Encoder(input_shape, n_blocks=4, nz_feat=nz_feat, z_dim=opts.z_dim)
        self.shape_predictor = ShapePredictor(opts.z_dim, num_verts=self.num_output)

        if(self.pred_cam):
            if opts.multiple_cam_hypo:
                self.cam_predictor = MultiCamPredictor(512, 8, 128, nz_feat=opts.nz_feat,
                                                       num_cams=opts.num_hypo_cams, aze_ele_quat=opts.az_ele_quat,
                                                       scale_lr=opts.scale_lr_decay, scale_bias=opts.scale_bias,
                                                       dataset = 'cub')
            else:
                self.cam_predictor = Camera(opts.nz_feat,)

        if self.pred_texture:
            if self.symmetric_texture:
                num_faces = self.num_indept_faces + self.num_sym_faces
            else:
                num_faces = faces.shape[0]

            uv_sampler = mesh.compute_uvsampler(verts_np, faces_np[:num_faces], tex_size=opts.tex_size)
            uv_sampler = torch.FloatTensor(uv_sampler).cuda()
            uv_sampler = uv_sampler.unsqueeze(0).repeat(int(self.opts.batch_size/self.opts.gpu_num), 1, 1, 1, 1)
            self.F = uv_sampler.size(1)
            self.T = uv_sampler.size(2)

            uv_sampler = uv_sampler.view(-1, self.F, self.T*self.T, 2)
            self.register_buffer('uv_sampler', uv_sampler)

            img_H = int(2**np.floor(np.log2(np.sqrt(num_faces) * opts.tex_size)))
            img_W = 2 * img_H
            self.num_faces = num_faces
            if(self.symmetric_texture):
                self.texture_predictor = TexturePredictorUV(
                  nz_feat, self.F, self.T, opts, img_H=img_H, img_W=img_W, predict_flow=True, symmetric=opts.symmetric_texture, num_sym_faces=self.num_sym_faces)
            else:
                self.texture_predictor = TexturePredictorUV(
                  nz_feat, self.F, self.T, opts, img_H=img_H, img_W=img_W, predict_flow=True, symmetric=opts.symmetric_texture)
            nb.net_init(self.texture_predictor)
            self.tex_size = opts.tex_size

        self.freeze_layers()

    def freeze_layers(self):
        # img_feat related
        for param in self.encoder.resnet_conv.parameters():
            param.requires_grad = False
        for param in self.encoder.enc_conv1.parameters():
            param.requires_grad = False
        for param in self.encoder.enc_fc.parameters():
            param.requires_grad = False
        for param in self.shape_predictor.parameters():
            param.requires_grad = False

    def forward(self, img=None, pred_vs=False):
        outputs = {}
        # reconstruct path
        img_feat, noise, mean, logvar = self.encoder(img)
        # print(mean,logvar)

        if(self.pred_cam):
            if self.opts.multiple_cam_hypo:
                cam_sampled, sample_inds, cam_probs, all_cameras, base_quats = self.cam_predictor.forward(img_feat)
                cam = cam_sampled
                outputs['cam_hypotheses'] = all_cameras
                outputs['base_quats'] = base_quats[:,0]
            else:
                cam = self.cam_predictor.forward(img_feat) ## quat (0:4), prop(4:5), scale(5:6), trans(6:8)
                cam = torch.cat([cam[:,5:6], cam[:, 6:8], cam[:,0:4]],dim=1)# scale(0) trans(1,2) quat(3,4,5,6)
                sample_inds = torch.zeros(cam[:, None, 0].shape).long().cuda()
                cam_probs = sample_inds.float() + 1

        outputs['mean'] = mean
        outputs['logvar'] = logvar
        outputs['cam_sample_inds'] = sample_inds
        outputs['cam_probs'] = cam_probs
        outputs['cam'] = cam
        outputs['noise'] = noise


        if self.pred_texture:
            if(self.uv_sampler.size(0) != img_feat.size(0)):
                uv_sampler = self.uv_sampler[0].unsqueeze(0).repeat(img_feat.size(0), 1, 1, 1)
                texture_pred, uvimage_pred = self.texture_predictor(img_feat, uv_sampler)
            else:
                texture_pred, uvimage_pred = self.texture_predictor(img_feat, self.uv_sampler)
            outputs['tex_flow'] = texture_pred
            outputs['uvimage_pred'] = uvimage_pred
        if pred_vs:
            shape_pred = self.shape_predictor(noise)
            outputs['delta_v'] = shape_pred

        return outputs

    def symmetrize(self, V):
        """
        Takes num_indept+num_sym verts and makes it
        num_indept + num_sym + num_sym
        Is identity if model is not symmetric
        """
        if self.symmetric:
            if V.dim() == 2:
                # No batch
                V_left = self.flip * V[-self.num_sym:]
                #return torch.cat([V, V_left], 0)
                return torch.cat([V, V_left], 0)
            else:
                # With batch
                V_left = self.flip * V[:, -self.num_sym:]
                return torch.cat([V, V_left], 1)
        else:
            return V

    def get_mean_shape(self):
        return self.symmetrize(self.mean_v)
