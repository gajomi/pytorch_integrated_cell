import torch
import torch.optim as optim
import torch.nn as nn
from torch.autograd import Variable
import numpy as np
import pdb


class trainer(object):
    def __init__(self, dp, opt):
        gpu_id = opt.gpu_ids[0]
        
        self.x = Variable(dp.get_images(range(0, opt.batch_size),'train')).cuda(gpu_id)
        
        if opt.nClasses > 0:
            self.classes = Variable(torch.LongTensor(opt.batch_size)).cuda(gpu_id)
        else:
            self.classes = None
            
        if opt.nRef > 0:
            self.classes = Variable(torch.LongTensor(opt.batch_size, opt.nRef)).cuda(gpu_id)
        else:
            self.ref = None
        
        self.zReal = Variable(torch.FloatTensor(opt.batch_size, opt.nlatentdim)).cuda(gpu_id)
        
        self.y_zReal = Variable(torch.ones(opt.batch_size)).cuda(gpu_id)
        self.y_zFake = Variable(torch.zeros(opt.batch_size)).cuda(gpu_id)
        
        if opt.nClasses > 0:
            self.y_xReal = self.classes
            self.y_xFake = Variable(torch.LongTensor(opt.batch_size)).cuda(gpu_id)
        else:
            self.y_xReal = self.y_zReal
            self.y_xFake = self.y_zFake
    
    def iteration(self, enc, dec, encD, decD, 
                  optEnc, optDec, optEncD, optDecD, 
                  critRecon, critZClass, critZRef, critEncD, critDecD,
                  dataProvider, opt):
        gpu_id = opt.gpu_ids[0]

        ###update the discriminator
        #maximize log(AdvZ(z)) + log(1 - AdvZ(Enc(x)))
        for p in encD.parameters(): # reset requires_grad
            p.requires_grad = True # they are set to False below in netG update

        for p in decD.parameters():
            p.requires_grad = True

        for p in enc.parameters():
            p.requires_grad = False

        for p in dec.parameters():
            p.requires_grad = False

        rand_inds_encD = np.random.permutation(opt.ndat)
        inds = rand_inds_encD[0:opt.batch_size]
        
        self.x.data.copy_(dataProvider.get_images(inds,'train'))
        x = self.x
        
        if opt.nClasses > 0:
            self.classes.data.copy_(dataProvider.get_classes(inds,'train'))
            x = self.classes

        if opt.nRef > 0:
            self.ref.data.copy_(dataProvider.get_ref(inds,'train'))
            ref = self.ref
            
        zAll = enc(x)
            
        for var in zAll:
            var.detach_()

        xHat = dec(zAll)

        self.zReal.data.copy_(opt.latentSample(opt.batch_size, opt.nlatentdim))
        zReal = self.zReal
        zFake = zAll[-1]

        optEnc.zero_grad()
        optDec.zero_grad()
        optEncD.zero_grad()
        optDecD.zero_grad()

        ### train encD
        y_zReal = self.y_zReal
        y_zFake = self.y_zFake

        # train with real
        yHat_zReal = encD(zReal)
        errEncD_real = critEncD(yHat_zReal, y_zReal)
        errEncD_real.backward(retain_variables=True)

        # train with fake
        yHat_zFake = encD(zFake)
        errEncD_fake = critEncD(yHat_zFake, y_zFake)
        errEncD_fake.backward(retain_variables=True)

        encDLoss = (errEncD_real + errEncD_fake)/2

        ###Train decD 
        if opt.nClasses > 0:
            y_xReal = classes
            self.y_xFake.data.fill_(opt.nClasses)
            y_xFake = self.yFake
        else:
            y_xReal = self.y_xReal
            y_xFake = self.y_xFake
            
        yHat_xReal = decD(x)
        errDecD_real = critDecD(yHat_xReal, y_xReal)
        # errDecD_real.backward(retain_variables=True)

        #train with fake, reconstructed
        yHat_xFake = decD(xHat)        
        errDecD_fake = critDecD(yHat_xFake, y_xFake)
        # errDecD_fake.backward(retain_variables=True)

        #train with fake, sampled and decoded
        zAll[-1] = zReal

        yHat_xFake2 = decD(dec(zAll))
        errEncD_fake2 = critDecD(yHat_xFake2, y_xFake)
        # errEncD_fake2.backward(retain_variables=True)

        decDLoss = (errDecD_real + (errDecD_fake + errEncD_fake2)/2)/2
        decDLoss.backward(retain_variables=True)
        optDecD.step()

        encDLoss = encDLoss.data[0]
        decDLoss = decDLoss.data[0]

#         yHat_zReal = None
#         yHat_zFake = None

        errEncD_real = None
        errEncD_fake = None

        errDecD_real = None
        errDecD_fake = None
        errEncD_fake2 = None

#         yHat_xReal = None
#         yHat_xFake = None
#         yHat_xFake2 = None

        for p in enc.parameters():
            p.requires_grad = True

        for p in dec.parameters():
            p.requires_grad = True  

        for p in encD.parameters():
            p.requires_grad = False

        for p in decD.parameters():
            p.requires_grad = False

        optEnc.zero_grad()
        optDec.zero_grad()
        optEncD.zero_grad()  
        optDecD.zero_grad()          

        ## train the autoencoder
        zAll = enc(x)
        xHat = dec(zAll)    

        c = 0        
        if opt.nClasses > 0:
            classLoss = critZClass(zAll[c], classes)
            classLoss.backward(retain_variables=True)        
            c += 1

        if opt.nRef > 0:
            refLoss = critZRef(zAll[c], ref)
            refLoss.backward(retain_variables=True)        
            c += 1

        reconLoss = critRecon(xHat, x)
        reconLoss.backward(retain_variables=True)        

        #update wrt encD
        yHatFake = encD(zAll[c])
        minimaxEncDLoss = critEncD(yHatFake, y_zReal)
        (minimaxEncDLoss.mul(opt.encDRatio)).backward(retain_variables=True)

        optEnc.step()

        minimaxEncDLoss = minimaxEncDLoss.data[0]

        for p in enc.parameters():
            p.requires_grad = False

        #update wrt decD(dec(enc(X)))
        yHat_xFake = decD(xHat)
        minimaxDecDLoss = critDecD(yHat_xFake, y_xReal)
        (minimaxDecDLoss.mul(opt.decDRatio)/2).backward(retain_variables=True)
        minimaxDecDLoss = minimaxDecDLoss.data[0]
        yHat_xFake = None
        
        #update wrt decD(dec(Z))
        self.zReal.data.copy_(opt.latentSample(opt.batch_size, opt.nlatentdim))
        zAll[c] = self.zReal
        xHat = dec(zAll)

        yHat_xFake2 = decD(xHat)
        minimaxDecDLoss2 = critDecD(yHat_xFake2, y_xReal)
        (minimaxDecDLoss2.mul(opt.decDRatio)/2).backward(retain_variables=True)
        minimaxDecDLoss2 = minimaxDecDLoss2.data[0]
        yHat_xFake2 = None
        
        minimaxDecLoss = (minimaxDecDLoss+minimaxDecDLoss2)/2
        
        optDec.step()
        
        

        errors = (reconLoss.data[0],)
        if opt.nClasses > 0:
            errors += (classLoss.data[0],)

        if opt.nRef > 0:
            errors += (refLoss.data[0],)

        errors += (minimaxEncDLoss, encDLoss, minimaxDecLoss, decDLoss)

        return errors, zFake.data
    