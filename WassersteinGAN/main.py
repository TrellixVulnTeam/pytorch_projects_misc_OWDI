from __future__ import print_function 
import argparse
import random
import os

import torch
import torch.nn as nn
import torch.nn.parallel # for multi-GPU training 
import torch.backends.cudnn as cudnn
import torch.optim as optim
import torch.utils.data
import torchvision.datasets as dset # provide interface for variaous datasets 
import torchvision.transforms as transforms # for data preprocessing 
import torchvision.utils as vutils # for saving minibatch of images(N x C x H x W) into grid of images 
from torch.autograd import Variable

import models.dcgan as dcgan
import models.mlp as mlp

import my_modules.utils as mutils

from IPython.core.debugger import Tracer 
debug_here = Tracer() 


parser = argparse.ArgumentParser()

# specify data and datapath 
parser.add_argument('--dataset', required=True, help='cifar10 | lsun | imagenet | folder | lfw ')
parser.add_argument('--dataroot', required=True, help='path to dataset')
# number of workers for loading data
parser.add_argument('--workers', type=int, help='number of data loading workers', default=2)
# loading data 
parser.add_argument('--batchSize', type=int, default=64, help='input batch size')
parser.add_argument('--imageSize', type=int, default=64, help='the height / width of the input image to network')
parser.add_argument('--nc', type=int, default=3, help='input image channels')
# spicify noise dimension to the Generator 
parser.add_argument('--nz', type=int, default=100, help='size of the latent z vector')
parser.add_argument('--ngf', type=int, default=64)
parser.add_argument('--ndf', type=int, default=64)
# spcify optimization stuff 
parser.add_argument('--adam', action='store_true', help='Whether to use adam (default is rmsprop)')
parser.add_argument('--max_epochs', type=int, default=140, help='number of epochs to train for')
parser.add_argument('--lrD', type=float, default=0.00005, help='learning rate for Critic, default=0.00005')
parser.add_argument('--lrG', type=float, default=0.00005, help='learning rate for Generator, default=0.00005')
parser.add_argument('--beta1', type=float, default=0.5, help='beta1 for adam. default=0.5')

parser.add_argument('--cuda'  , action='store_true', help='enables cuda')
parser.add_argument('--ngpu'  , type=int, default=1, help='number of GPUs to use')

parser.add_argument('--gpu_id'  , type=str, default='1', help='which gpu to use, used only when ngpu is 1')

parser.add_argument('--Diters', type=int, default=5, help='number of D iters per each G iter')
parser.add_argument('--noBN', action='store_true', help='use batchnorm or not (only for DCGAN)')
parser.add_argument('--n_extra_layers', type=int, default=0, help='Number of extra layers on gen and discriminator')

parser.add_argument('--mlp_G', action='store_true', help='use MLP for G')
parser.add_argument('--mlp_D', action='store_true', help='use MLP for D')

# clamp parameters into a cube 
parser.add_argument('--clamp_lower', type=float, default=-0.01)
parser.add_argument('--clamp_upper', type=float, default=0.01)

parser.add_argument('--experiment', default=None, help='Where to store samples and models')

# resume training from a checkpoint
parser.add_argument('--netG', default='', help="path to netG (to continue training)")
parser.add_argument('--netD', default='', help="path to netD (to continue training)")
parser.add_argument('--optim_state_from', default='', help="optim state to resume training")


opt = parser.parse_args()
print(opt)

if opt.experiment is None:
    opt.experiment = 'models_and_samples'

os.system('mkdir {0}'.format(opt.experiment))

# must set this variables before any initialization 
os.environ['CUDA_VISIBLE_DEVICES'] = opt.gpu_id

ngpu = int(opt.ngpu)

# opt.manualSeed = random.randint(1, 10000) # fix seed
opt.manualSeed = 123456

if torch.cuda.is_available() and not opt.cuda:
    print("WARNING: You have a CUDA device, so you should probably run with --cuda")
else:
    if ngpu == 1: 
        print('so we use 1 gpu to training') 
        print('setting gpu on gpuid {0}'.format(opt.gpu_id))

        if opt.cuda:
            torch.cuda.manual_seed(opt.manualSeed)

print("Random Seed: ", opt.manualSeed)
random.seed(opt.manualSeed)
torch.manual_seed(opt.manualSeed)



cudnn.benchmark = True

if opt.dataset in ['imagenet', 'folder', 'lfw']:
    # folder dataset
    dataset = dset.ImageFolder(root=opt.dataroot,
                               transform=transforms.Compose([
                                   transforms.Scale(opt.imageSize),
                                   transforms.CenterCrop(opt.imageSize),
                                   transforms.ToTensor(),
                                   transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5)),
                               ]))

elif opt.dataset == 'lsun':
    dataset = dset.LSUN(db_path=opt.dataroot, classes=['bedroom_train'],
                        transform=transforms.Compose([
                            transforms.Scale(opt.imageSize),
                            transforms.CenterCrop(opt.imageSize),
                            transforms.ToTensor(),
                            transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5)),
                        ]))

elif opt.dataset == 'cifar10':
    # by default, self.train is True, dataset only use training set
    dataset = dset.CIFAR10(root=opt.dataroot, download=True,
                           transform=transforms.Compose([
                               transforms.Scale(opt.imageSize),
                               transforms.ToTensor(),
                               transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5)),
                           ])
    )

## transforms.ToTensor():
# """Converts a PIL.Image or numpy.ndarray (H x W x C) in the range
# [0, 255] to a torch.FloatTensor of shape (C x H x W) in the range [0.0, 1.0]. """
##
assert dataset

# dataset.train_data: (50000, 32, 32, 3), ie, data format is HWC
# dataset[1] will give image of size 3 x 64 x 64 and label is 9, for example (img = Image.fromarray(img) in 
# pytorch/vision/dataset/cifar.py)

# set shuffle to be True, so that before every epoch of training, we 
# shuffle the datasets
dataloader = torch.utils.data.DataLoader(dataset, batch_size=opt.batchSize,
                                         shuffle=True, num_workers=int(opt.workers))

nz = int(opt.nz)
ngf = int(opt.ngf)
ndf = int(opt.ndf)
nc = int(opt.nc) # 3 by default, i.e., for RGB images
n_extra_layers = int(opt.n_extra_layers)

# custom weights initialization called on netG and netD
def weights_init(m):
    classname = m.__class__.__name__
    if classname.find('Conv') != -1: # classname not contains 'Conv', then return -1, otherwise, return 0 
        m.weight.data.normal_(0.0, 0.02)
    elif classname.find('BatchNorm') != -1:
        m.weight.data.normal_(1.0, 0.02)
        m.bias.data.fill_(0)

if opt.noBN: # GCGAN without batch normalization layers 
    netG = dcgan.DCGAN_G_nobn(opt.imageSize, nz, nc, ngf, ngpu, n_extra_layers)
elif opt.mlp_G:
    netG = mlp.MLP_G(opt.imageSize, nz, nc, ngf, ngpu)
else:
    netG = dcgan.DCGAN_G(opt.imageSize, nz, nc, ngf, ngpu, n_extra_layers)

# doing initialization 
netG.apply(weights_init)
print(netG)

if opt.mlp_D:
    # Just MLP, so no need to initialize
    netD = mlp.MLP_D(opt.imageSize, nz, nc, ndf, ngpu) 
else:
    netD = dcgan.DCGAN_D(opt.imageSize, nz, nc, ndf, ngpu, n_extra_layers)
    netD.apply(weights_init)

print(netD)

# initialize from checkpoints 
if opt.netD != '':
    print('loading checkpoints from {0}'.format(opt.netD))
    netD.load_state_dict(torch.load(opt.netD))
if opt.netG != '': # load checkpoint if needed
    print('loading checkpoints from {0}'.format(opt.netG))
    netG.load_state_dict(torch.load(opt.netG))

# input: b x c x h x w
# for cifar10, it is: bz x 3 x 64 x 64 (scaled version)
# note that even though cifar10 image is of size 3 x 32 x 32
input = torch.FloatTensor(opt.batchSize, 3, opt.imageSize, opt.imageSize)
noise = torch.FloatTensor(opt.batchSize, nz, 1, 1)

# to have fixed_noise when looking output of generators 
# to evaluate the training progress 
# 1. For sample looking:
# 2. For learning curves:
fixed_noise = torch.FloatTensor(opt.batchSize, nz, 1, 1).normal_(0, 1)

one = torch.FloatTensor([1])
mone = one * -1

# shift model and data to GPU 
if opt.cuda:
    netD.cuda()
    netG.cuda()
    input = input.cuda()
    one, mone = one.cuda(), mone.cuda()
    noise, fixed_noise = noise.cuda(), fixed_noise.cuda()

# setup optimizer
if opt.adam: # false 
    optimizerD = optim.Adam(netD.parameters(), lr=opt.lrD, betas=(opt.beta1, 0.999))
    optimizerG = optim.Adam(netG.parameters(), lr=opt.lrG, betas=(opt.beta1, 0.999))
else:
    optimizerD = optim.RMSprop(netD.parameters(), lr = opt.lrD)
    optimizerG = optim.RMSprop(netG.parameters(), lr = opt.lrG)

# loading optim state 

epoch = 0
if opt.optim_state_from != '':
    print('loading optim_state_from {0}'.format(opt.optim_state_from))
    optim_state = torch.load(opt.optim_state_from)
    epoch = optim_state['epoch']
    # configure optimzer 
    optimizerG.load_state_dict(optim_state['optimizerG_state'])
    optimizerD.load_state_dict(optim_state['optimizerD_state'])

############
# debug_here() 
############
gen_iterations = 0

# for epoch in range(opt.max_epochs):


while epoch < opt.max_epochs:
   
    epoch = epoch + 1 
    # Return an iterator object
    # object must be a collection object which supports the iteration protocol (the __iter__() method), 
    # or it must support the sequence protocol (the __getitem__() method with integer arguments 
    # starting at 0).
    data_iter = iter(dataloader)

    i = 0
    while i < len(dataloader): # running one epoch 

        ############################
        # (1) Update D network
        ###########################
        for p in netD.parameters(): # reset requires_grad
            p.requires_grad = True  # they are set to False below in netG update

        # train the discriminator Diters times
        if gen_iterations < 25 or gen_iterations % 500 == 0:
            Diters = 100 
        else:
            Diters = opt.Diters # 5, i.e., train Determinator 5 
                                # iterations every 1 iteration training of Generator

        j = 0
        while j < Diters and i < len(dataloader):
            j += 1

            # clamp parameters to a cube
            for p in netD.parameters():
                p.data.clamp_(opt.clamp_lower, opt.clamp_upper)

            # load one batch of data 
            data = data_iter.next() # i-th batch 
            i += 1

            # train with real
            real_cpu, _ = data
            netD.zero_grad()
            batch_size = real_cpu.size(0)

            if opt.cuda:
                real_cpu = real_cpu.cuda()

            input.resize_as_(real_cpu).copy_(real_cpu)
            inputv = Variable(input)

            errD_real = netD(inputv)
            
            errD_real.backward(one)

            # train with fake
            # Gaussian noise 
            noise.resize_(opt.batchSize, nz, 1, 1).normal_(0, 1)
            
            noisev = Variable(noise, volatile = True) # totally freeze netG
            fake = Variable(netG(noisev).data)
            inputv = fake
            errD_fake = netD(inputv)
            errD_fake.backward(mone)

            errD = errD_real - errD_fake
            optimizerD.step()

        ############################
        # (2) Update G network
        ###########################
        for p in netD.parameters():
            p.requires_grad = False # to avoid computation

        netG.zero_grad()

        # in case our last batch was the tail batch of the dataloader,
        # make sure we feed a full batch of noise
        noise.resize_(opt.batchSize, nz, 1, 1).normal_(0, 1)
        noisev = Variable(noise)
        fake = netG(noisev)
        # error on fake image
        # just a scalar 
        errG = netD(fake) 
        errG.backward(one)
        optimizerG.step()
        gen_iterations += 1

        print('[%d/%d][%d/%d][%d] Loss_D: %f Loss_G: %f Loss_D_real: %f Loss_D_fake %f'
            % (epoch, opt.max_epochs, i, len(dataloader), gen_iterations,
            errD.data[0], errG.data[0], errD_real.data[0], errD_fake.data[0]))

        if gen_iterations % 500 == 0:
            print('Saving current real images ... ')
            # convert back 
            real_cpu = real_cpu.mul(0.5).add(0.5)
            ##########
            # debug_here() 
            ##########
            # check https://github.com/pytorch/vision/blob/master/torchvision/utils.py#L81
            # which will convert the tensor type to value of rang (0, 255)
            vutils.save_image(real_cpu, '{0}/real_samples.png'.format(opt.experiment))
            fake = netG(Variable(fixed_noise, volatile=True))
            fake.data = fake.data.mul(0.5).add(0.5)
            print('Now we begin to generate images ... ')
            vutils.save_image(fake.data, '{0}/fake_samples_{1}.png'.format(opt.experiment, gen_iterations))


    ##############################################################################
    ## save checkpoints every 1 epoch, including netG, netD, and optim_state for optimizer
    ##############################################################################
    # save checkpoint every 1 epoch 
    # do checkpointing
    path_G = '{0}/netG_epoch_{1}.pth'.format(opt.experiment, epoch%5)
    path_D = '{0}/netD_epoch_{1}.pth'.format(opt.experiment, epoch%5)
    # save models to checkpoint
    mutils.save_checkpoint(netG.state_dict(), path_G)
    mutils.save_checkpoint(netD.state_dict(), path_D)
    # save optim_state 
    path_optim_state = '{0}/optim_state_epoch_{1}.pth'.format(opt.experiment, epoch%5)
    optim_state = {} 
    optim_state['epoch'] = epoch 
    # save ids instead of prameters variables 
    # for state, it will save a dictionary of id to variables 
    optim_state['optimizerG_state'] = optimizerG.state_dict() 
    optim_state['optimizerD_state'] = optimizerD.state_dict() 
    mutils.save_checkpoint(optim_state, path_optim_state)


