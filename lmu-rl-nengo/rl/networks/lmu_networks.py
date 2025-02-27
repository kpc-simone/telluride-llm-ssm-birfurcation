import nengo
import numpy as np
import scipy.linalg
from scipy.special import legendre


###############################3
#     simple LMU as a process: transforms done exactly, useful if creating custom time loop, cannot be used in a nengo network
#############################

class LMU():
    def __init__(self, theta, q, dt, size_in=1):
        self.q = q              # number of internal state dimensions per input
        self.theta = theta      # size of time window (in seconds)
        self.size_in = size_in
        self.dt = dt

        # Do Aaron's math to generate the matrices
        #  https://github.com/arvoelke/nengolib/blob/master/nengolib/synapses/analog.py#L536
        Q = np.arange(q, dtype=np.float64)
        R = (2*Q + 1)[:, None] / theta
        j, i = np.meshgrid(Q, Q)

        self.A = np.where(i < j, -1, (-1.)**(i-j+1)) * R
        self.B = (-1.)**Q[:, None] * R

        # discretize A, B
        self.Ad = scipy.linalg.expm(self.A*dt)
        self.Bd = np.dot(np.dot(np.linalg.inv(self.A), (self.Ad-np.eye(self.q))), self.B)

        self.state = np.zeros((self.q, self.size_in))
            
        super().__init__()

    def step(self, x, reset=False):
        if reset:
            self.state = np.dot(self.Bd, np.atleast_2d(x))
        else:
            self.state = np.dot(self.Ad, self.state) + np.dot(self.Bd, np.atleast_2d(x))


###############################3
#     LMU as a process: transforms done exactly, you can put this in a node
#     This is Terry's code from learn_dyn_sys
#############################


class LMUProcess(nengo.Process):
    def __init__(self, theta, q, size_in=1, with_resets=False, with_holds=False):
        self.q = q              # number of internal state dimensions per input
        self.theta = theta      # size of time window (in seconds)
        self.size_in = size_in
        self.state_size = size_in
        if with_resets:
            size_in = size_in+1
        if with_holds:
            size_in = size_in+1
        # Do Aaron's math to generate the matrices
        #  https://github.com/arvoelke/nengolib/blob/master/nengolib/synapses/analog.py#L536
        Q = np.arange(q, dtype=np.float64)
        R = (2*Q + 1)[:, None] / theta
        j, i = np.meshgrid(Q, Q)

        self.A = np.where(i < j, -1, (-1.)**(i-j+1)) * R
        self.B = (-1.)**Q[:, None] * R

        self.t=0
        self.with_resets = with_resets
        self.with_holds = with_holds
        super().__init__(default_size_in=size_in, default_size_out=q*self.state_size)

    def make_step(self, shape_in, shape_out, dt, rng, state=None):
        state = np.zeros((self.q, self.state_size))
        
        Ad = scipy.linalg.expm(self.A*dt)
        Bd = np.dot(np.dot(np.linalg.inv(self.A), (Ad-np.eye(self.q))), self.B)
        # this code will be called every timestep
        if self.with_resets & self.with_holds:
            def step_legendre(t, x, state=state):
                if (x[0]!=0) & (x[1] != 0): # reset & hold
                    state[:] =  0
                elif (x[0]==0) & (x[1] != 0):   # reset, no hold
                    state[:] = np.dot(Bd, x[None, 2:])
                elif (x[0]==0): # update normally
                    state[:] = np.dot(Ad, state) + np.dot(Bd, x[None, 2:])
                # otherwise: state unchanged, hold no reset
                return state.T.flatten()
        elif self.with_resets:
            def step_legendre(t, x, state=state):
                if x[0] != 0:
                    state[:] = np.dot(Bd, x[None, 1:])
                else:
                    state[:] = np.dot(Ad, state) + np.dot(Bd, x[None, 1:])
                return state.T.flatten()
        elif self.with_holds:
            def step_legendre(t, x, state=state):
                if x[0] == 0:
                    state[:] = np.dot(Ad, state) + np.dot(Bd, x[None, 1:])
                return state.T.flatten()
        else:
            def step_legendre(t, x, state=state):
                state[:] = np.dot(Ad, state) + np.dot(Bd, x[None, :])
                return state.T.flatten()
        return step_legendre

    def get_weights_for_delays(self, r):
        # compute the weights needed to extract the value at time r
        # from the network (r=0 is right now, r=1 is theta seconds ago)
        r = np.asarray(r)
        m = np.asarray([legendre(i)(2*r - 1) for i in range(self.q)])
        return m.reshape(self.q, -1).T
    
    

    
#######################################################
#      LMU via a recurrent neural network
##################################################3


class LMUNetwork(nengo.Network):
    def __init__(self, n_neurons, theta, q, size_in=1,tau=0.05,**kwargs):
        super().__init__()
        
        self.q = q              # number of internal state dimensions per input
        self.theta = theta      # size of time window (in seconds)
        self.size_in = size_in  # number of inputs

        # Do Aaron's math to generate the matrices
        #  https://github.com/arvoelke/nengolib/blob/master/nengolib/synapses/analog.py#L536
        Q = np.arange(q, dtype=np.float64)
        R = (2*Q + 1)[:, None] / theta
        j, i = np.meshgrid(Q, Q)

        self.A = np.where(i < j, -1, (-1.)**(i-j+1)) * R
        self.B = (-1.)**Q[:, None] * R
        
        with self:
            self.input = nengo.Node(size_in=size_in)
            self.reset = nengo.Node(size_in=1)
            
            self.lmu = nengo.networks.EnsembleArray(n_neurons, n_ensembles=size_in, 
                                                    ens_dimensions=q, **kwargs)
            self.output = self.lmu.output            
            for i in range(size_in):
                nengo.Connection(self.input[i], self.lmu.ea_ensembles[i], synapse=tau,
                                 transform = tau*self.B)
                nengo.Connection(self.lmu.ea_ensembles[i], self.lmu.ea_ensembles[i], synapse=tau,
                                 transform = tau*self.A + np.eye(q))
                nengo.Connection(self.reset, self.lmu.ea_ensembles[i].neurons, transform = [[-2.5]]*n_neurons, synapse=None)

   
    