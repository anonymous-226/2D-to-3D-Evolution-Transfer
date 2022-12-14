# -----------------------------------------------------------
# Code adapted from: https://github.com/akanazawa/cmr/blob/master/utils/image.py
# 
# MIT License
# 
# Copyright (c) 2018 akanazawa
# 
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# 
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
# -----------------------------------------------------------

# Mesh stuff.
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import numpy as np
import meshzoo
import torch


def create_sphere(n_subdivide=3):
    # 3 makes 642 verts, 1280 faces,
    # 4 makes 2562 verts, 5120 faces
    verts, faces = meshzoo.iso_sphere(n_subdivide)
    return verts, faces


def make_symmetric(verts, faces, axis=0):
    """
    Assumes that the input mesh {V,F} is perfectly symmetric
    Splits the mesh along the X-axis, and reorders the mesh s.t.
    (so this is reflection on Y-axis..?)
    [indept verts, right (x>0) verts, left verts]

    v[:num_indept + num_sym] = A
    v[:-num_sym] = -A[num_indept:]
    """
    left = verts[:, axis] < 0
    right = verts[:, axis] > 0
    center = verts[:, axis] == 0


    left_inds = np.where(left)[0]
    right_inds = np.where(right)[0]
    center_inds = np.where(center)[0]

    num_indept = len(center_inds)
    num_sym = len(left_inds)
    assert(len(left_inds) == len(right_inds))

    # For each right verts, find the corresponding left verts.
    if(axis == 0):
        prop_left_inds = np.hstack([np.where(np.all(verts == np.array([-1, 1, 1]) * verts[ri], 1))[0] for ri in right_inds])
    elif(axis == 1):
        prop_left_inds = np.hstack([np.where(np.all(verts == np.array([1, -1, 1]) * verts[ri], 1))[0] for ri in right_inds])
    else:
        prop_left_inds = np.hstack([np.where(np.all(verts == np.array([1, 1, -1]) * verts[ri], 1))[0] for ri in right_inds])
    assert(prop_left_inds.shape[0] == num_sym)

    # Make sure right/left order are symmetric.
    for ind, (ri, li) in enumerate(zip(right_inds, prop_left_inds)):
        if(axis == 0):
            if np.any(verts[ri] != np.array([-1, 1, 1]) * verts[li]):
                print('bad! %d' % ind)
                import ipdb; ipdb.set_trace()
        elif(axis == 1):
            if np.any(verts[ri] != np.array([1, -1, 1]) * verts[li]):
                print('bad! %d' % ind)
                import ipdb; ipdb.set_trace()
        else:
            if np.any(verts[ri] != np.array([1, 1, -1]) * verts[li]):
                print('bad! %d' % ind)
                import ipdb; ipdb.set_trace()

    new_order = np.hstack([center_inds, right_inds, prop_left_inds])
    # verts i is now vert j
    ind_perm = np.hstack([np.where(new_order==i)[0] for i in range(verts.shape[0])])

    new_verts = verts[new_order, :]
    new_faces0 = ind_perm[faces]

    new_faces, num_indept_faces, num_sym_faces = make_faces_symmetric(new_verts, new_faces0, num_indept, num_sym, axis)

    return new_verts, new_faces, num_indept, num_sym, num_indept_faces, num_sym_faces

def make_faces_symmetric(verts, faces, num_indept_verts, num_sym_verts, axis=0):
    """
    This reorders the faces, such that it has this order:
      F_indept - independent face ids
      F_right (x>0)
      F_left

    1. For each face, identify whether it's independent or has a symmetric face.

    A face is independent, if v_i is an independent vertex and if the other two v_j, v_k are the symmetric pairs.
    Otherwise, there are two kinds of symmetric faces:
    - v_i is indept, v_j, v_k are not the symmetric paris)
    - all three have symmetric counter verts.

    Returns a new set of faces that is in the above order.
    Also, the symmetric face pairs are reordered so that the vertex order is the same.
    i.e. verts[f_id] and verts[f_id_sym] is in the same vertex order, except the x coord are flipped
    """
    DRAW = False
    indept_faces = []
    right_faces = []
    left_faces = []

    indept_verts = verts[:num_indept_verts]
    symmetric_verts = verts[num_indept_verts:]
    # These are symmetric pairs
    right_ids = np.arange(num_indept_verts, num_indept_verts+num_sym_verts)
    left_ids = np.arange(num_indept_verts+num_sym_verts, num_indept_verts+2*num_sym_verts)
    # Make this for easy lookup
    # Saves for each vert_id, the symmetric vert_ids
    v_dict = {}
    for r_id, l_id in zip(right_ids, left_ids):
        v_dict[r_id] = l_id
        v_dict[l_id] = r_id
    # Return itself for indepentnet.
    for ind in range(num_indept_verts):
        v_dict[ind] = ind

    # Saves faces that contain this verts
    verts2faces = [np.where((faces == v_id).any(axis=1))[0] for v_id in range(verts.shape[0])]
    done_face = np.zeros(faces.shape[0])
    # Make faces symmetric:
    for f_id in range(faces.shape[0]):
        if done_face[f_id]:
            continue
        v_ids = sorted(faces[f_id])
        # This is triangles x [x,y,z]
        vs = verts[v_ids]
        # Find the corresponding vs?
        v_sym_ids = sorted([v_dict[v_id] for v_id in v_ids])

        # Check if it's independent
        if sorted(v_sym_ids) == sorted(v_ids):
            # Independent!!
            indept_faces.append(faces[f_id])
            # indept_faces.append(f_id)
            done_face[f_id] = 1
        else:
            # Find the face with these verts. (so we can mark it done)
            possible_faces = np.hstack([verts2faces[v_id] for v_id in v_sym_ids])
            possible_fids, counts = np.unique(possible_faces, return_counts=True)
            # The face id is the one that appears 3 times in this list.
            sym_fid = possible_fids[counts == 3][0]
            assert(sorted(v_sym_ids) == sorted(faces[sym_fid]))
            # Make sure that the order of these vertices are the same.
            # Go in the order of face: f_id
            face_here = faces[f_id]
            sym_face_here = [v_dict[v_id] for v_id in face_here]
            # Above is the same tri as faces[sym_fid], but vertices are in the order of faces[f_id]
            # Which one is right x > 0?
            # Only use unique verts in these faces to compute.
            unique_vids = np.array(v_ids) != np.array(v_sym_ids)
            #if np.all(verts[face_here][unique_vids, 0] < verts[sym_face_here][unique_vids, 0]):
            if np.all(verts[face_here][unique_vids, axis] < verts[sym_face_here][unique_vids, axis]):
                # f_id is left
                left_faces.append(face_here)
                right_faces.append(sym_face_here)
            else:
                left_faces.append(sym_face_here)
                right_faces.append(face_here)
            done_face[f_id] = 1
            done_face[sym_fid] = 1
            # Draw
            # tri_sym = Mesh(verts[v_sym_ids], [[0, 1, 2]], vc='red')
            # mv.set_dynamic_meshes([mesh, tri, tri_sym])

    assert(len(left_faces) + len(right_faces) + len(indept_faces) == faces.shape[0])
    # Now concatenate them,,
    new_faces = np.vstack([indept_faces, right_faces, left_faces])
    # Now sort each row of new_faces to make sure that bary centric coord will be same.
    num_indept_faces = len(indept_faces)
    num_sym_faces = len(right_faces)

    return new_faces, num_indept_faces, num_sym_faces

def get_spherical_coords(X):
    # X is N x 3
    rad = np.linalg.norm(X, axis=1)
    # Inclination
    theta = np.arccos(X[:, 2] / rad)
    # Azimuth
    phi = np.arctan2(X[:, 1], X[:, 0])

    # Normalize both to be between [-1, 1]
    vv = (theta / np.pi) * 2 - 1
    uu = ((phi + np.pi) / (2*np.pi)) * 2 - 1
    # Return N x 2
    return np.stack([uu, vv],1)


def compute_uvsampler(verts, faces, tex_size=2):
    """
    For this mesh, pre-computes the UV coordinates for
    F x T x T points.
    Returns F x T x T x 2
    """
    alpha = np.arange(tex_size, dtype=np.float) / (tex_size-1)
    beta = np.arange(tex_size, dtype=np.float) / (tex_size-1)
    import itertools
    # Barycentric coordinate values
    coords = np.stack([p for p in itertools.product(*[alpha, beta])])
    vs = verts[faces] # 1280 x 3 x 3
    # Compute alpha, beta (this is the same order as NMR)
    v2 = vs[:, 2]
    v0v2 = vs[:, 0] - vs[:, 2]
    v1v2 = vs[:, 1] - vs[:, 2]
    # F x 3 x T*2
    samples = np.dstack([v0v2, v1v2]).dot(coords.T) + v2.reshape(-1, 3, 1)
    # F x T*2 x 3 points on the sphere
    samples = np.transpose(samples, (0, 2, 1))

    # Now convert these to uv.
    uv = get_spherical_coords(samples.reshape(-1, 3))

    uv = uv.reshape(-1, tex_size, tex_size, 2)
    return uv
