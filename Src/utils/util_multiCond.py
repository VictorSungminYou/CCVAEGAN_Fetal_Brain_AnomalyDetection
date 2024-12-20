import argparse
import os, time
from collections import OrderedDict
import torch
from torch.utils.data import Dataset, DataLoader, Subset
import numpy as np
import imutils
import operator
import os
import csv
import nibabel as nib


from functools import lru_cache

class DatasetSingleton:
    _instance = None
    ga_dict = {}
    sex_dict = {}

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(DatasetSingleton, cls).__new__(cls)
            cls._initialize_data(*args, **kwargs)
        return cls._instance

    @classmethod
    def _initialize_data(cls, csv_paths):
        for data_type, path in csv_paths.items():
            with open(path, 'r') as csvfile:
                csvreader = csv.DictReader(csvfile)
                for row in csvreader:
                    key = row['ID']
                    if data_type == 'ga':
                        cls.ga_dict[key] = float(row['GA'])
                    elif data_type == 'sex':
                        if row['Sex']=='Male':
                            cls.sex_dict[key] = [1,0]
                        elif row['Sex']=='Female':
                            cls.sex_dict[key] = [0,1]
                        elif row['Sex']=='Unknown':
                            cls.sex_dict[key] = [0,0]

class img_dataset(Dataset):

    # Begin the initialization of the datasets. Creates dataset iterativey for each subject and
    # concatenates them together for both training and testing datasets (implements img_dataset class).
    def __init__(self, root_dir, view, key, data = 'healthy', size: int = 158, horizontal_flip: bool = False, 
                 vertical_flip: bool = False, rotation_angle: int = None, raw = False, ga_info = None):
        self.root_dir = root_dir
        self.view = view
        self.horizontal = horizontal_flip
        self.vertical = vertical_flip
        self.angle = rotation_angle
        self.size = size
        self.key = key
        if 'recon' in self.key:
            self.key = self.key[:self.key.index('recon')-1]
        self.data = data
        self.raw = raw
        
        if ga_info:
            self.dataset_singleton = DatasetSingleton({'ga': ga_info, 'sex': ga_info})
        else:
            self.dataset_singleton = DatasetSingleton({
                'ga': os.getcwd() + '/TD_data_proceed.csv',
                'sex': os.getcwd() + '/TD_data_proceed.csv'
            })
            
        #print(self.dataset_singleton.ga_dict)


    def __len__(self):
        view_sizes = {'L': 158, 'A': 158, "S": 158}
        return view_sizes.get(self.view, 0)
    
    # def extract_id(self):
    #     subject_number_ID = self.dataset_singleton.id_dict.get(self.key)
    #     if subject_number_ID is None:
    #         print(f"subject_number_ID not found for {self.key}")
    #         raise Exception(f"subject_number_ID not found for {self.key}")
        
    #     subject_number_ID = np.expand_dims(subject_number_ID, axis=0)
    #     subject_number_ID = torch.tensor(subject_number_ID).type(torch.float)
    #     return subject_number_ID

    def extract_sex(self):
        sex = self.dataset_singleton.sex_dict.get(self.key)
        if sex is None:
            print(f"Sex not found for {self.key}")
            raise Exception(f"Sex not found for {self.key}")
        
        sex = torch.tensor(sex).type(torch.float)
        return sex
        
    def extract_age(self):
        ga = self.dataset_singleton.ga_dict.get(self.key)
        if ga is None:
            print(f"GA not found for {self.key}")
            raise Exception(f"GA not found for {self.key}")
        
        ga = np.expand_dims(ga, axis=0)
        ga = torch.tensor(ga).type(torch.float)
        return ga
    
    def rotation(self, x, alpha):
        y = x.astype(np.uint8)
        y_rot = imutils.rotate(y, angle = alpha)
        return y_rot.astype(np.float64)
    
    def resizing(self, img, n):
        target = (n, n)
        if (img.shape > np.array(target)).any():
            target_shape2 = np.min([target, img.shape],axis=0)
            start = tuple(map(lambda a, da: a//2-da//2, img.shape, target_shape2))
            end = tuple(map(operator.add, start, target_shape2))
            slices = tuple(map(slice, start, end))
            img = img[tuple(slices)]
        offset = tuple(map(lambda a, da: a//2-da//2, target, img.shape))
        slices = [slice(offset[dim], offset[dim] + img.shape[dim]) for dim in range(img.ndim)]
        result = np.zeros(target)
        result[tuple(slices)] = img
        return result
    
    def adjust_image(self, img, target_size):
        from scipy.ndimage import center_of_mass, shift
        original_size = np.array(img.shape)
        padding_needed = np.array(target_size) - original_size
        padding_needed = np.maximum(padding_needed, 0)
        padding_before = padding_needed // 2
        padding_after = padding_needed - padding_before

        # Apply padding
        padded_img = np.pad(img, ((padding_before[0], padding_after[0]), (padding_before[1], padding_after[1])), 'constant', constant_values=0)

        brain_mask = padded_img > 0
        brain_com = center_of_mass(brain_mask)

        padded_center = np.array(padded_img.shape) / 2
        shift_amount = padded_center - np.array(brain_com)

        shifted_img = shift(padded_img, shift=shift_amount)
        current_size = np.array(shifted_img.shape)
        crop_needed = current_size - np.array(target_size)
        crop_before = crop_needed // 2
        crop_after = crop_needed - crop_before
        # Ensure cropping indices are non-negative
        crop_before = np.maximum(crop_before, 0)
        crop_after = np.maximum(crop_after, 0)
        # Apply cropping
        final_img = shifted_img[crop_before[0]:current_size[0]-crop_after[0], crop_before[1]:current_size[1]-crop_after[1]]

        return final_img

    def normalize_95(self, x):
        p98 = np.percentile(x, 99)
        num = x-np.min(x)
        den = p98-np.min(x)
        out = np.zeros((x.shape[0], x.shape[1]))

        x = np.divide(num, den, out=out, where=den!=0)
        return x.clip(0, 1)
    

    def clip_and_resize(self, img, n, percentile=50):
        
        # Continue with the existing resizing process
        target = (n, n)
        if (img.shape > np.array(target)).any():
            target_shape2 = np.min([target, img.shape], axis=0)
            start = tuple(map(lambda a, da: a//2 - da//2, img.shape, target_shape2))
            end = tuple(map(operator.add, start, target_shape2))
            slices = tuple(map(slice, start, end))
            img = img[tuple(slices)]
        offset = tuple(map(lambda a, da: a//2 - da//2, target, img.shape))
        slices = [slice(offset[dim], offset[dim] + img.shape[dim]) for dim in range(img.ndim)]
        result = np.zeros(target)
        result[tuple(slices)] = img
        # Calculate the threshold intensity for the specified percentile
        threshold = np.percentile(result, percentile)
        
        # Set all pixels below this threshold to zero
        result[result < threshold] = 0
        return result

    @lru_cache(maxsize=10000)
    def __getitem__(self, idx):
        nii_file = nib.load(self.root_dir, mmap=True)
        
        if self.view == 'L':
            slice_data = nii_file.dataobj[idx, :, :]
        elif self.view == 'A':
            slice_data = nii_file.dataobj[:, idx, :]
        else:
            slice_data = nii_file.dataobj[:, :, idx]
        
        slice_data = np.array(slice_data)
        
        n_img = self.clip_and_resize(slice_data, self.size, percentile=10)
        n_img = self.normalize_95(n_img)

        if self.horizontal:
            n_img = np.flip(n_img, axis=0)
        if self.vertical:
            n_img = np.flip(n_img, axis=1)
        if self.angle is not None:
            n_img = self.rotation(n_img, self.angle)

        n_img = np.expand_dims(n_img, axis=0)
        img_torch = torch.from_numpy(n_img.copy()).type(torch.float)

        ga = self.extract_age()
        sex = self.extract_sex()

        return {'image': img_torch, 'ga': ga, 
                'sex': sex, 
                'key': self.key}

def center_slices(view):
    if view == 'L':
        ids = np.arange(start=40,stop=70)
    elif view == 'A':
        ids = np.arange(start=64,stop=94)
    else:
        ids = np.arange(start=48,stop=78)
    return ids

def data_augmentation(base_set, path, view, key, h, ids):
    transformations = {1: (True, None),
                       2: (False, -10), 3: (True, -10),
                       4: (False, -5), 5: (True, -5),
                       6: (False, 5), 7: (True, 5),
                       8: (False, 10), 9: (True, 10)}
    
    for x, specs in transformations.items():
        aug = img_dataset(path, view, key, size = h, horizontal_flip = specs[0], rotation_angle = specs[1])
        aug = Subset(aug,ids)
        base_set = torch.utils.data.ConcatDataset([base_set, aug])
    return base_set

def loader(source_path, view, batch_size, h, raw = False):
    train_id = os.listdir(source_path+'train/')
    test_id = os.listdir(source_path+'test/')

    ids = center_slices(view)
    train_set = img_dataset(source_path+'train/'+train_id[0], view, train_id[0][:-4], size = h, raw = raw)
    train_set = Subset(train_set,ids)
    # train_set = data_augmentation(train_set, source_path+'train/'+train_id[0], view, 
    #                               train_id[0][:-4], h, ids)

    test_set = img_dataset(source_path+'test/'+test_id[0],view, test_id[0][:-4], size = h, raw = raw)
    test_set = Subset(test_set,ids)

    for idx,image in enumerate(train_id):
        if idx != 0:
            train_path = source_path + 'train/' + image
            tr_set = img_dataset(train_path, view, image[:-4], size = h, raw = raw)
            tr_set = Subset(tr_set,ids)
            # tr_set = data_augmentation(tr_set, train_path, view, image[:-4], h, ids)
            train_set = torch.utils.data.ConcatDataset([train_set, tr_set])

    for idx,image in enumerate(test_id):
        if idx != 0:
            test_path = source_path + 'test/' + image
            ts_set = img_dataset(test_path,view, image[:-4], size = h, raw = raw)
            ts_set = Subset(ts_set,ids)
            test_set = torch.utils.data.ConcatDataset([test_set, ts_set])

# Dataloaders generated from datasets 
    train_final = DataLoader(train_set, shuffle=True, batch_size=batch_size,num_workers=12,drop_last=True)
    val_final = DataLoader(test_set, shuffle=True, batch_size=batch_size,num_workers=12)
    return train_final, val_final

def old_loader(source_path, view, batch_size, h, raw = False):
    train_id = os.listdir(source_path+'train_old/')
    test_id = os.listdir(source_path+'test_old/')

    ids = center_slices(view)
    train_set = img_dataset(source_path+'train_old/'+train_id[0], view, train_id[0][:-4], size = h, raw = raw)
    train_set = Subset(train_set,ids)
    # train_set = data_augmentation(train_set, source_path+'train/'+train_id[0], view, 
    #                               train_id[0][:-4], h, ids)

    test_set = img_dataset(source_path+'test_old/'+test_id[0],view, test_id[0][:-4], size = h, raw = raw)
    test_set = Subset(test_set,ids)

    for idx,image in enumerate(train_id):
        if idx != 0:
            train_path = source_path + 'train_old/' + image
            tr_set = img_dataset(train_path, view, image[:-4], size = h, raw = raw)
            tr_set = Subset(tr_set,ids)
            # tr_set = data_augmentation(tr_set, train_path, view, image[:-4], h, ids)
            train_set = torch.utils.data.ConcatDataset([train_set, tr_set])

    for idx,image in enumerate(test_id):
        if idx != 0:
            test_path = source_path + 'test_old/' + image
            ts_set = img_dataset(test_path,view, image[:-4], size = h, raw = raw)
            ts_set = Subset(ts_set,ids)
            test_set = torch.utils.data.ConcatDataset([test_set, ts_set])

# Dataloaders generated from datasets 
    train_final = DataLoader(train_set, shuffle=True, batch_size=batch_size,num_workers=12,drop_last=True)
    val_final = DataLoader(test_set, shuffle=True, batch_size=batch_size,num_workers=12)
    return train_final, val_final

def test_loader(source_path, view, batch_size, h, raw = False):
    test_id = os.listdir(source_path+'/')

    ids = center_slices(view)
    test_set = img_dataset(source_path+'/'+test_id[0], view, test_id[0][:-4], size = h, raw = raw)
    test_set = Subset(test_set,ids)

    test_data_list = []
    for idx,image in enumerate(test_id):
        if idx != 0:
            test_path = source_path + '/' + image
            ts_set = img_dataset(test_path,view, image[:-4], size = h, raw = raw)
            ts_set = Subset(ts_set,ids)
            test_set = torch.utils.data.ConcatDataset([test_set, ts_set])
            test_data_list.append(image[:-4])
            
    # Dataloaders generated from datasets 
    test_final = DataLoader(test_set, shuffle=False, batch_size=batch_size, num_workers=12)
    return test_final, test_data_list

def val_loader(val_path, images, view, data='healthy', raw=False):
    ids = center_slices(view)

    val_set = img_dataset(val_path+images[0], view, images[0][:-4], data=data, raw=raw)
    val_set = Subset(val_set, ids)

    for idx, image in enumerate(images):
        if idx != 0: 
            v_set = img_dataset(val_path+image, view, image[:-4], data=data, raw=raw)
            v_set = Subset(v_set, ids)
            val_set = torch.utils.data.ConcatDataset([val_set, v_set])
    
    loader = DataLoader(val_set, batch_size=1)

    return loader

def load_model_IP_VAEGAN(model_path, ga_method, w, h, z_dim, model='default', ga_n = 100, BOE_form = 'BOE'):
    prCyan(f'{ga_method=}')
    from models.IP_VAEGAN import Encoder, Decoder
    encoder = Encoder(h, w, z_dim, method = ga_method, model = model, ga_n = ga_n, BOE_form = BOE_form)
    decoder = Decoder(h, w, int(z_dim/2) + (ga_n if ga_method in ['ordinal_encoding', 'one_hot_encoding', 'boe'] else 0))
    cpe = torch.load(model_path+'encoder_best.pth', map_location=torch.device('cpu'))
    cpd = torch.load(model_path+'decoder_best.pth', map_location=torch.device('cpu'))
    # cpe = torch.load(model_path+'encoder_latest.pth', map_location=torch.device('cpu'))
    # cpd = torch.load(model_path+'decoder_latest.pth', map_location=torch.device('cpu'))
    try:
        encoder.load_state_dict(cpe['encoder'])
        decoder.load_state_dict(cpd['decoder'])
        return encoder, decoder
    except Exception as e:
        raise NameError(f'Pre-trained model did not load properly: {e}')

def load_model(model_path, base, ga_method, w, h, z_dim, model='default', pre = False, ga_n = 100, BOE_form = 'BOE'):
    prCyan(f'{ga_method=}')
    if base == 'ga_VAE':
        # from models.ga_vae import Encoder, Decoder
        from models.SI_VAE import Encoder, Decoder
        encoder = Encoder(h, w, z_dim, method = ga_method, model = model, ga_n = ga_n, BOE_form = BOE_form)
    else:
        from models.vae import Encoder, Decoder
        encoder = Encoder(h, w, z_dim, model=model)
    decoder = Decoder(h, w, int(z_dim/2) + (ga_n if ga_method in ['ordinal_encoding', 'one_hot_encoding', 'boe'] else 0))

    cpe = torch.load(model_path+'encoder_best.pth', map_location=torch.device('cpu'))
    cpd = torch.load(model_path+'decoder_best.pth', map_location=torch.device('cpu'))
    # cpe = torch.load(model_path+'encoder_latest.pth', map_location=torch.device('cpu'))
    # cpd = torch.load(model_path+'decoder_latest.pth', map_location=torch.device('cpu'))

    cpe_new = OrderedDict()
    cpd_new = OrderedDict()

    import models.aotgan.aotgan as inpainting
    refineG = inpainting.InpaintGenerator(BOE_size=0, BOE_form = BOE_form)#BOE_size=0)
    refineD = inpainting.Discriminator(BOE_size=0, BOE_form = BOE_form)#BOE_size=0)
    cp_refG = torch.load(model_path+'refineG_best.pth', map_location=torch.device('cpu'))
    cp_refD = torch.load(model_path+'refineD_best.pth', map_location=torch.device('cpu'))
    # cp_refG = torch.load(model_path+'refineG_latest.pth', map_location=torch.device('cpu'))
    # cp_refD = torch.load(model_path+'refineD_latest.pth', map_location=torch.device('cpu'))

    cp_refG_new = OrderedDict()
    cp_refD_new = OrderedDict()

    if pre == 'base':
        for k, v in cpe['encoder'].items():
            name = k[:]
            cpe_new[name] = v

        for k, v in cpd['decoder'].items():
            name = k[:]
            cpd_new[name] = v

        encoder.load_state_dict(cpe_new)
        decoder.load_state_dict(cpd_new)
        return encoder, decoder
    else:
        for k, v in cp_refG['refineG'].items():
            name = k
            cp_refG_new[name] = v

        refineG.load_state_dict(cp_refG_new)

        if pre == 'full':
            for k, v in cpe['encoder'].items():
                name = k
                if (k=='linear.weight' or k=='linear.bias') and ga_method not in ['ordinal_encoding', 'one_hot_encoding', 'boe']:
                    cpe_new[name] = v[:511]
                else:
                    cpe_new[name] = v

            for k, v in cpd['decoder'].items():
                name = k
                cpd_new[name] = v

            encoder.load_state_dict(cpe_new)
            decoder.load_state_dict(cpd_new)

            return encoder, decoder, refineG
        elif pre == 'refine':
            for k, v in cp_refD['refineD'].items():
                name = k
                cp_refD_new[name] = v

            refineD.load_state_dict(cp_refD_new)
            return refineG, refineD
        else:
            raise NameError('Pre-trained model did not load properly')
        
def load_model_new(model_path, base, ga_method, w, h, z_dim, model='default', pre = False, ga_n = 100, BOE_form = 'BOE'):
    prCyan(f'{ga_method=}')
    if base == 'ga_VAE':
        # from models.ga_vae import Encoder, Decoder
        from models.SI_VAE import Encoder, Decoder
        encoder = Encoder(h, w, z_dim, method = ga_method, model = model, ga_n = ga_n, BOE_form = BOE_form)
    else:
        from models.vae import Encoder, Decoder
        encoder = Encoder(h, w, z_dim, model=model)
    decoder = Decoder(h, w, int(z_dim/2) + (ga_n if ga_method in ['ordinal_encoding', 'one_hot_encoding', 'boe'] else 0))

    cpe = torch.load(model_path+'encoder_best.pth', map_location=torch.device('cpu'))
    cpd = torch.load(model_path+'decoder_best.pth', map_location=torch.device('cpu'))
    # cpe = torch.load(model_path+'encoder_latest.pth', map_location=torch.device('cpu'))
    # cpd = torch.load(model_path+'decoder_latest.pth', map_location=torch.device('cpu'))

    cpe_new = OrderedDict()
    cpd_new = OrderedDict()

    import models.aotgan.aotgan_new as inpainting
    refineG = inpainting.InpaintGenerator(BOE_size=200, BOE_form = BOE_form)#BOE_size=0)
    refineD = inpainting.Discriminator(BOE_size=200, BOE_form = BOE_form)#BOE_size=0)
    # cp_refG = torch.load(model_path+'refineG_best.pth', map_location=torch.device('cpu'))
    # cp_refD = torch.load(model_path+'refineD_best.pth', map_location=torch.device('cpu'))
    cp_refG = torch.load(model_path+'refineG_latest.pth', map_location=torch.device('cpu'))
    cp_refD = torch.load(model_path+'refineD_latest.pth', map_location=torch.device('cpu'))

    cp_refG_new = OrderedDict()
    cp_refD_new = OrderedDict()

    if pre == 'base':
        for k, v in cpe['encoder'].items():
            name = k[:]
            cpe_new[name] = v

        for k, v in cpd['decoder'].items():
            name = k[:]
            cpd_new[name] = v

        encoder.load_state_dict(cpe_new)
        decoder.load_state_dict(cpd_new)
        return encoder, decoder
    else:
        for k, v in cp_refG['refineG'].items():
            name = k
            cp_refG_new[name] = v

        refineG.load_state_dict(cp_refG_new)

        if pre == 'full':
            for k, v in cpe['encoder'].items():
                name = k
                if (k=='linear.weight' or k=='linear.bias') and ga_method not in ['ordinal_encoding', 'one_hot_encoding', 'boe']:
                    cpe_new[name] = v[:511]
                else:
                    cpe_new[name] = v

            for k, v in cpd['decoder'].items():
                name = k
                cpd_new[name] = v

            encoder.load_state_dict(cpe_new)
            decoder.load_state_dict(cpd_new)

            return encoder, decoder, refineG
        elif pre == 'refine':
            for k, v in cp_refD['refineD'].items():
                name = k
                cp_refD_new[name] = v

            refineD.load_state_dict(cp_refD_new)
            return refineG, refineD
        else:
            raise NameError('Pre-trained model did not load properly')
        
def load_model_VAEAOTGAN(model_path, ga_method, w, h, z_dim, model='default', ga_n = 100, BOE_form = 'BOE'):
    prCyan(f'{ga_method=}')
    from models.VAE_GAN import Encoder, Decoder
    encoder = Encoder(h, w, z_dim, method = ga_method, model = model, ga_n = ga_n, BOE_form = BOE_form)
    decoder = Decoder(h, w, int(z_dim/2) + (ga_n if ga_method in ['ordinal_encoding', 'one_hot_encoding', 'boe'] else 0))
    cpe = torch.load(model_path+'encoder_best.pth', map_location=torch.device('cpu'))
    cpd = torch.load(model_path+'decoder_best.pth', map_location=torch.device('cpu'))
    # cpe = torch.load(model_path+'encoder_latest.pth', map_location=torch.device('cpu'))
    # cpd = torch.load(model_path+'decoder_latest.pth', map_location=torch.device('cpu'))
    try:
        encoder.load_state_dict(cpe['encoder'])
        decoder.load_state_dict(cpd['decoder'])
        return encoder, decoder
    except Exception as e:
        raise NameError(f'Pre-trained model did not load properly: {e}')

def load_model_cycle(model_path, base, ga_method, w, h, z_dim, model='default', pre = False, ga_n = 100, BOE_form = 'BOE'):
    prCyan(f'{ga_method=}')
    if base == 'ga_VAE':
        # from models.ga_vae import Encoder, Decoder
        from models.SI_VAE import Encoder, Decoder
        encoder = Encoder(h, w, z_dim, method = ga_method, model = model, ga_n = ga_n, BOE_form = BOE_form)
    else:
        from models.vae import Encoder, Decoder
        encoder = Encoder(h, w, z_dim, model=model)
    decoder = Decoder(h, w, int(z_dim/2) + (ga_n if ga_method in ['ordinal_encoding', 'one_hot_encoding', 'boe'] else 0))

    cpe = torch.load(model_path+'encoder_best.pth', map_location=torch.device('cpu'))
    cpd = torch.load(model_path+'decoder_best.pth', map_location=torch.device('cpu'))
    # cpe = torch.load(model_path+'encoder_latest.pth', map_location=torch.device('cpu'))
    # cpd = torch.load(model_path+'decoder_latest.pth', map_location=torch.device('cpu'))

    cpe_new = OrderedDict()
    cpd_new = OrderedDict()

    import models.aotgan.aotgan as inpainting
    refineG = inpainting.InpaintGenerator(BOE_size=200, BOE_form = BOE_form)#BOE_size=0)
    refineD = inpainting.Discriminator(BOE_size=200, BOE_form = BOE_form)#BOE_size=0)
    # cp_refG = torch.load(model_path+'refineG_best.pth', map_location=torch.device('cpu'))
    # cp_refD = torch.load(model_path+'refineD_best.pth', map_location=torch.device('cpu'))
    cp_refG = torch.load(model_path+'refineG_latest.pth', map_location=torch.device('cpu'))
    cp_refD = torch.load(model_path+'refineD_latest.pth', map_location=torch.device('cpu'))

    cp_refG_new = OrderedDict()
    cp_refD_new = OrderedDict()

    if pre == 'base':
        for k, v in cpe['encoder'].items():
            name = k[:]
            cpe_new[name] = v

        for k, v in cpd['decoder'].items():
            name = k[:]
            cpd_new[name] = v

        encoder.load_state_dict(cpe_new)
        decoder.load_state_dict(cpd_new)
        return encoder, decoder
    else:
        for k, v in cp_refG['refineG'].items():
            name = k
            cp_refG_new[name] = v

        refineG.load_state_dict(cp_refG_new)

        if pre == 'full':
            for k, v in cpe['encoder'].items():
                name = k
                if (k=='linear.weight' or k=='linear.bias') and ga_method not in ['ordinal_encoding', 'one_hot_encoding', 'boe']:
                    cpe_new[name] = v[:511]
                else:
                    cpe_new[name] = v

            for k, v in cpd['decoder'].items():
                name = k
                cpd_new[name] = v

            encoder.load_state_dict(cpe_new)
            decoder.load_state_dict(cpd_new)

            return encoder, decoder, refineG
        elif pre == 'refine':
            for k, v in cp_refD['refineD'].items():
                name = k
                cp_refD_new[name] = v

            refineD.load_state_dict(cp_refD_new)
            return refineG, refineD
        else:
            raise NameError('Pre-trained model did not load properly')

def path_generator(args):
    # Define paths for obtaining dataset and saving models and results.
    source_path = args.path + args.training_folder
        
    folder_name = args.name+'_'+args.view
    #folder_pretrained = args.pre_n+'_'+args.view

    tensor_path = args.path + 'Results/' + folder_name + '/history.txt'
    model_path = args.path + 'Results/' + folder_name + '/Saved_models/'
    image_path = args.path + 'Results/' + folder_name + '/Progress/'
    #pre_path = args.path + 'Results/' + folder_pretrained + '/Saved_models/'

    if not os.path.exists(args.path + 'Results/' + folder_name):
        os.mkdir(args.path + 'Results/' + folder_name)
        os.mkdir(model_path)
        os.mkdir(image_path)

    # if (args.pretrained) and (not os.path.exists(pre_path)):
    #     print(pre_path)
    #     raise NameError("model_path for pretraining is not correct.")

    print('Directories and paths are correctly initialized.')
    print('-'*25)
    return source_path, model_path, tensor_path, image_path

def settings_parser():
    parser = argparse.ArgumentParser()
    
    parser.add_argument('--task',
        dest='task',
        choices=['Train', 'Validate', 'Visualize', 'Train_CycleGAN', 'Train_GAN', 'Train_Test', 'Train_IPVAEGAN'],
        required=False,
        default='Train',
        help='''
        Task to be performed.''')  
    parser.add_argument('--VAE_model_type',
        dest='VAE_model_type',
        choices=['default', 'ga_VAE'],
        default = 'default',
        required=False,
        help='''
        Type of model to train. Available options:
        "defalut" Default VAE using convolution blocks
        "ga_VAE: VAE which includes GA as input''') 
    parser.add_argument('--model_type',
        dest='type',
        choices=['default', 'bVAE'],
        required=True,
        help='''
        Type of model to train. Available options:
        "defalut" Default VAE using convolution blocks
        "bVAE: VAE with disentanglement''')  
    parser.add_argument('--model_view',
        dest='view',
        choices=['L', 'A', 'S'],
        required=True,
        help='''
        The view of the image input for the model. Options:
        "L" Left view
        "A" Axial view
        "S" Sagittal view''') 
    parser.add_argument('--ga_method',
        dest='ga_method',
        choices=['multiplication', 'concat', 'concat_sample', 'ordinal_encoding', 'one_hot_encoding', 'boe'],
        default = 'concat',
        required=False,
        help='''
        Method to implement GA. Available options:
        "multiplication", "concat"''') 
    parser.add_argument('--gpu',
        dest='gpu',
        choices=['0', '1', '2'],
        default='0',
        required=False,
        help='''
        The GPU that will be used for training. Terminals have the following options:
        Hanyang: 0, 1
        Busan: 0, 1, 2
        Sejong 0, 1, 2
        Songpa 0, 1
        Gangnam 0, 1
        ''')
    parser.add_argument('--epochs',
        dest='epochs',
        type=int,
        default=2000,
        required=False,
        help='''
        Number of epochs for training.
        ''')    
    parser.add_argument('--loss',
        dest='loss',
        default='L2',
        choices=['L2', 'L1', 'SSIM', 'MS_SSIM'],
        required=False,
        help='''
        Loss function for VAE:
        L2 = Mean square error.
        SSIM = Structural similarity index.
        ''')
    parser.add_argument('--batch',
        dest='batch',
        type=int,
        default=32,
        choices=[2**x for x in range(8)],
        required=False,
        help='''
        Number of batch size.
        ''') 
    parser.add_argument('--z_dim',
        dest='z_dim',
        type=int,
        default=512,
        required=False,
        help='''
        z dimension.
        ''')
    parser.add_argument('--pretrained',
        dest='pretrained',
        type=str,
        default=None,
        choices=['base','refine'],
        required=False,
        help='''
        If VAE model is pre-trained.
        ''')
    parser.add_argument('--pre_name',
        dest='pre_n',
        type=str,
        default='Tlaloc',
        required=False,
        help='''
        Name of pre-trained VAE model.
        '''
            )
    parser.add_argument('--name',
        dest='name',
        type=str,
        required=True,
        help='''
        Name for new VAE model.
        '''
            )
    parser.add_argument('--slice_size',
        dest='slice_size',
        type=int,
        default=158,
        required=False,
        help='''
        Size of images from pre-processing (n x n).
        ''')
    parser.add_argument('--path',
        dest = 'path',
        type = str,
        default = './',
        required = False,
        help='''
        Path to the project directory
        ''')
    parser.add_argument('--training_folder',
        dest = 'training_folder',
        type = str,
        default = 'TD_dataset/',
        required = False,
        help='''
        Path to the project directory
        ''')
    parser.add_argument(
        '-ga_n',
        '--GA_encoding_dimensions', 
        dest='ga_n',
        type=int,
        default=100,
        required=False,
        help='''
        Size of vector for ga representation.
        ''')
    parser.add_argument(
        '-raw',
        '--raw_data', 
        dest='raw',
        action='store_true',
        required=False,
        help='''
        Training or testing on raw data.
        ''')
    parser.add_argument(
        '-th',
        '--threshold', 
        dest='th',
        type=int,
        default=99,
        help='''
        Treshold for the mask.
        ''')
    parser.add_argument(
        '-cGAN',
        '--conditional_GAN', 
        dest='cGAN',
        action='store_true',
        required=False,
        help='''
        BOE implemented to the GD as a cGAN.
        ''')
    parser.add_argument(
        '-cGAN_s',
        '--conditional_GAN_size', 
        dest='cGAN',
        action='store_true',
        required=False,
        help='''
        BOE implemented to the GD as a cGAN.
        ''')
    parser.add_argument('--beta_kl',
        dest='beta_kl',
        type=float,
        default=None,
        required=False,
        help='''
        The value of the beta KL parameter.
        ''')
    parser.add_argument('--beta_rec',
        dest='beta_rec',
        type=float,
        default=None,
        required=False,
        help='''
        The value of the beta rec parameter.
        ''')
    parser.add_argument('--beta_neg',
        dest='beta_kl',
        type=float,
        default=None,
        required=False,
        help='''
        The value of the beta neg parameter.
        ''')
    parser.add_argument('--BOE_type',
        dest='BOE_type',
        default='BOE',
        choices=['BOE', 'EBOE', 'inv_BOE', 'inv_inv_BOE'],
        required=False,
        help='''
        BOE type.
        ''')
    

    return parser