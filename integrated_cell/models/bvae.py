import torch
import torch.optim as optim
import torch.nn as nn
from torch.autograd import Variable
import numpy as np
import pdb


from integrated_cell.model_utils import *
from integrated_cell.utils import plots as plots

from integrated_cell.models import base_model 

# This is the trainer for the Beta-VAE

def reparameterize(mu, log_var, add_noise = True):
    if add_noise:
        std = log_var.div(2).exp()
        eps = torch.randn_like(std)
        out = eps.mul(std).add_(mu)
    else:
        out = mu
        
    return out

def kl_divergence(mu, logvar):
    batch_size = mu.size(0)
    assert batch_size != 0
    if mu.data.ndimension() == 4:
        mu = mu.view(mu.size(0), mu.size(1))
        
    if logvar.data.ndimension() == 4:
        logvar = logvar.view(logvar.size(0), logvar.size(1))

    klds = -0.5*(1 + logvar - mu.pow(2) - logvar.exp())
    total_kld = klds.sum(1).mean(0, True)
    dimension_wise_kld = klds.mean(0)
    mean_kld = klds.mean(1).mean(0, True)

    return total_kld, dimension_wise_kld, mean_kld



class Model(base_model.Model):
    def __init__(self, data_provider, n_channels, batch_size, n_latent_dim, n_classes, n_ref, gpu_ids, beta = 1, beta_step = None, objective = 'H'):
        super(Model, self).__init__(data_provider, n_channels, batch_size, n_latent_dim, n_classes, n_ref, gpu_ids)
        
        if beta_step is None:
            self.beta = beta
            self.beta_step = None
        else:
            self.beta = 0
            self.beta_step = beta_step
            self.beta_max = beta
            
        self.objective = objective

    def iteration(self,
                  enc, dec,
                  optEnc, optDec,
                  critRecon, critZClass, critZRef,
                  data_provider, opt):
        gpu_id = self.gpu_ids[0]
        
        
    
        rand_inds_encD = np.random.permutation(opt.ndat)
        inds = rand_inds_encD[0:self.batch_size]

        self.x.data.copy_(data_provider.get_images(inds,'train'))
        x = self.x

        #####################
        ### train autoencoder
        #####################

        enc.train(True)
        dec.train(True)
        
        optEnc.zero_grad()
        optDec.zero_grad()

        ### Forward passes
        zAll = enc(x)

        c = 0
        ### Update the class discriminator
        if self.n_classes > 0:
            classLoss = critZClass(zAll[c], classes)
            classLoss.backward(retain_graph=True)
            classLoss = classLoss.data[0]
            c += 1

        ### Update the reference shape discriminator
        if self.n_ref > 0:
            refLoss = critZRef(zAll[c], ref)
            refLoss.backward(retain_graph=True)
            refLoss = refLoss.data[0]
            c += 1

        total_kld, dimension_wise_kld, mean_kld = kl_divergence(zAll[c][0], zAll[c][1])
        

        zLatent = zAll[c][0]
        zAll[c] = reparameterize(zAll[c][0], zAll[c][1])

        xHat = dec(zAll)

        ### Update the image reconstruction
        reconLoss = critRecon(xHat, x)

        if self.beta == 0:
            beta_vae_loss = reconLoss
        elif self.objective == 'H':
            beta_vae_loss = reconLoss + self.beta*total_kld
        elif self.objective == 'B':
            C = torch.clamp(self.C_max/self.C_stop_iter*self.global_iter, 0, self.C_max.data[0])
            beta_vae_loss = recon_loss + self.gamma*(total_kld-C).abs()

        beta_vae_loss.backward()
        
        kld_loss = mean_kld.data[0]
        reconLoss = reconLoss.data[0]

        optEnc.step()
        optDec.step()

        errors = (reconLoss,)
        if self.n_classes > 0:
            errors += (classLoss,)

        if self.n_ref > 0:
            errors += (refLoss,)

        errors += (kld_loss,)
        
        if self.beta_step is not None:
            self.beta += self.beta_step
            
            if self.beta > self.beta_max:
                self.beta = self.beta_max

        return errors, zLatent.data


    def load(self, model_name, opt):

        model_provider = importlib.import_module("integrated_cell.networks." + model_name)

        enc = model_provider.Enc(self.n_latent_dim, self.n_classes, self.n_ref, self.n_channels, self.gpu_ids, **opt.kwargs_enc)
        dec = model_provider.Dec(self.n_latent_dim, self.n_classes, self.n_ref, self.n_channels, self.gpu_ids, **opt.kwargs_dec)
       
        enc.apply(weights_init)
        dec.apply(weights_init)
       
        gpu_id = self.gpu_ids[0]

        enc.cuda(gpu_id)
        dec.cuda(gpu_id)
       
        if opt.optimizer == 'RMSprop':
            optEnc = optim.RMSprop(enc.parameters(), lr=opt.lrEnc)
            optDec = optim.RMSprop(dec.parameters(), lr=opt.lrDec)
        elif opt.optimizer == 'adam':

            optEnc = optim.Adam(enc.parameters(), lr=opt.lrEnc, **opt.kwargs_optim)
            optDec = optim.Adam(dec.parameters(), lr=opt.lrDec, **opt.kwargs_optim)

        columns = ('epoch', 'iter', 'reconLoss',)
        print_str = '[%d][%d] reconLoss: %.6f'

        if self.n_classes > 0:
            columns += ('classLoss',)
            print_str += ' classLoss: %.6f'

        if self.n_ref > 0:
            columns += ('refLoss',)
            print_str += ' refLoss: %.6f'

        columns += ('kldLoss', 'time')
        print_str += ' kld: %.6f time: %.2f'

        logger = SimpleLogger(columns,  print_str)

        if os.path.exists('{0}/enc.pth'.format(opt.save_dir)):
            print('Loading from ' + opt.save_dir)

            load_state(enc, optEnc, '{0}/enc.pth'.format(opt.save_dir), gpu_id)
            load_state(dec, optDec, '{0}/dec.pth'.format(opt.save_dir), gpu_id)

            logger = pickle.load(open( '{0}/logger.pkl'.format(opt.save_dir), "rb" ))

            this_epoch = max(logger.log['epoch']) + 1
            iteration = max(logger.log['iter'])

        models = dict()
        models['enc'] = enc
        models['dec'] = dec

        optimizers = dict()
        optimizers['optEnc'] = optEnc
        optimizers['optDec'] = optDec

        criterions = dict()
        criterions['critRecon'] = eval('nn.' + opt.critRecon + '(size_average=False)')
        criterions['critZClass'] = nn.NLLLoss()
        criterions['critZRef'] = nn.MSELoss()

        if opt.latentDistribution == 'uniform':
            from integrated_cell.model_utils import sampleUniform as latentSample
        elif opt.latentDistribution == 'gaussian':
            from integrated_cell.model_utils import sampleGaussian as latentSample

        self.latentSample = latentSample
        
        self.models = models
        self.optimizers = optimizers
        self.criterions = criterions
        self.logger = logger
        self.opt = opt
        
        return models, optimizers, criterions, logger

    def save(self, enc, dec,
                   optEnc, optDec,
                   logger, zAll, opt):
    #         for saving and loading see:
    #         https://discuss.pytorch.org/t/how-to-save-load-torch-models/718

        gpu_id = self.gpu_ids[0]

        save_state(enc, optEnc, '{0}/enc.pth'.format(opt.save_dir), gpu_id)
        save_state(dec, optDec, '{0}/dec.pth'.format(opt.save_dir), gpu_id)

        pickle.dump(zAll, open('{0}/embedding.pkl'.format(opt.save_dir), 'wb'))
        pickle.dump(logger, open('{0}/logger.pkl'.format(opt.save_dir), 'wb'))
