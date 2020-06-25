# -*- coding: utf-8 -*-

# Imports

import pandas as pd
import numpy as np

from gpytorch.priors import GammaPrior

from .models import GP_Model
from .base_models import fast_computation
from .init_scheme import Init
from .objective import objective
from .acq_func import acquisition
from .plot_utils import plot_convergence
from .pd_utils import to_torch

# Main class definition

class BO:
    """Main method for calling Bayesian optimization algorithm.
    
    Class provides a unified framework for selecting experimental 
    conditions for the parallel optimization of chemical reactions
    and for the simulation of known objectives. The algorithm is 
    implemented on a user defined grid of domain points and is
    flexible to any numerical encoding.
    """
        
    def __init__(self,                 
                 results_path=None, results=pd.DataFrame(),
                 domain_path=None, domain=pd.DataFrame(),
                 exindex_path=None, exindex=pd.DataFrame(),
                 model=GP_Model, acquisition_function='EI', init_method='rand', 
                 target=-1, batch_size=5, duplicate_experiments=False, 
                 gpu=False, fast_comp=False, noise_constraint=1e-5,
                 matern_nu=2.5, lengthscale_prior=[GammaPrior(2.0, 0.2), 5.0],
                 outputscale_prior=[GammaPrior(5.0, 0.5), 8.0],
                 noise_prior=[GammaPrior(1.5, 0.5), 1.0],
                 computational_objective=None
                 ):
        
        """
        Experimental results, experimental domain, and experiment index of 
        known results can be passed as paths to .csv or .xlsx files or as 
        DataFrames.
        
        Parameters
        ----------
        results_path : str, optional
            Path to experimental results.
        results : pandas.DataFrame, optional
            Experimental results with X values matching the domain.
        domain_path : str, optional
            Path to experimental domain.
            
            Note
            ----
            A domain_path or domain are required.
            
        domain : pandas.DataFrame, optional
            Experimental domain specified as a matrix of possible configurations.
        exindex_path : str, optional
            Path to experiment results index if available.
        exindex : pandas.DataFrame, optional
            Experiment results index matching domain format. Used as lookup 
            table for simulations.
        model bro.models: 
            Surrogate model object used for Bayesian optimization. 
            See bro.models for predefined models and specification of custom
            models.
        acquisition_function : str 
            Acquisition function used for for selecting a batch of domain 
            points to evaluate. Options: (TS) Thompson Sampling, ('EI') 
            Expected Improvement, (PI) Probability of Improvement, (UCB) 
            Upper Confidence Bound, (EI-TS) EI (first choice) + TS (n-1 choices), 
            (PI-TS) PI (first choice) + TS (n-1 choices), (UCB-TS) UCB (first 
            choice) + TS (n-1 choices), (MeanMax-TS) Mean maximization 
            (first choice) + TS (n-1 choices), (VarMax-TS) Variance 
            maximization (first choice) + TS (n-1 choices), (MeanMax) 
            Top predicted values, (VarMax) Variance maximization, (rand) 
            Random selection.
        init_method : str 
            Strategy for selecting initial points for evaluation. 
            Options: (rand) Random selection, (pam) k-medoids algorithm, 
            (kmeans) k-means algorithm, (external) User define external data
            read in as results.
        target : str
            Column label of optimization objective. If set to -1, the last 
            column of the DataFrame will be set as the target.
        batch_size : int
            Number of experiments selected via acquisition and initialization 
            functions.
        duplicate_experiments : bool 
            Allow the acquisition function to select experiments already 
            present in results. 
        gpu : bool
            Carry out GPyTorch computations on a GPU if available.
        fast_comp : bool 
            Enable fast computation features for GPyTorch models.
        noise_constraint : float
            Noise constraint for GPyTorch models.
        matern_nu : 0.5, 1.5, 2.5
            Parameter value for model Matern kernel.
        lengthscale_prior : [gytorch.prior, initial_value]
            Specify a prior over GP length scale prameters.
        outputscale_prior : [gytorch.prior, initial_value]
            Specify a prior over GP output scale prameter.
        noise_prior : [gytorch.prior, initial_value]
            Specify a prior over GP noice prameter.
        computational_objective : function, optional
            Function to be optimized for computational objectives.
            
        """
        
        # Fast computation
        self.fast_comp = fast_comp
        fast_computation(fast_comp)
        
        # Initialize data container
        self.obj = objective(results_path=results_path, 
                             results=results, 
                             domain_path=domain_path, 
                             domain=domain, 
                             exindex_path=exindex_path, 
                             exindex=exindex, 
                             target=target, 
                             gpu=gpu,
                             computational_objective=computational_objective)
        
        # Initialize acquisition function
        self.acq = acquisition(acquisition_function, 
                              batch_size=batch_size, 
                              duplicates=duplicate_experiments)
        
        # Initialize experiment init sequence
        self.init_seq = Init(init_method, batch_size)
        
        # Initialize other stuff
        self.base_model = model # before eval for retraining
        self.model = model      # slot for after eval
        self.batch_size = batch_size
        self.duplicate_experiments = duplicate_experiments
        self.gpu = gpu
        self.proposed_experiments = pd.DataFrame()
        self.nu = matern_nu
        self.noise_constraint = noise_constraint
        self.lengthscale_prior = lengthscale_prior
        self.outputscale_prior = outputscale_prior
        self.noise_prior = noise_prior
        
    # Initial samples using init sequence
    def init_sample(self, seed=None, append=False, export_path=None):
        """Generate initial samples via an initialization method.
        
        Parameters
        ----------
        seed : None, int
            Random seed used for selecting initial points.
        append : bool
            Append points to results if computational objective or experiment
            index are available.
        export_path : str 
            Path to export SVG of clustering results if pam or kmeans methods 
            are used for selecting initial points.
        
        Returns
        ----------
        pandas.DataFrame
            Domain points for proposed experiments.
        """
        
        # Run initialization sequence
        if self.init_seq.method != 'external':
            self.obj.clear_results()
        self.proposed_experiments = self.init_seq.run(self.obj, 
                                                      seed=seed, 
                                                      export_path=export_path)
        
        # Append to know results
        if append == True and self.init_seq.method != 'external':
            self.obj.get_results(self.proposed_experiments, append=append)

        return self.proposed_experiments
        
    # Run algorithm and get next round of experiments
    def run(self, append=False, n_restarts=0, learning_rate=0.1,
            training_iters=100):
        """Run a single iteration of optimization with known results.
        
        Note
        ----
        Use run for human-in-the-loop optimization.
        
        Parameters
        ----------
        append : bool
            Append points to results if computational objective or experiment
            index are available.
        n_restarts : int
            Number of restarts used when optimizing GPyTorch model parameters.
        learning_rate : float
            ADAM learning rate used when optimizing GPyTorch model parameters.
        training_iters : int
            Number of iterations to run ADAM when optimizin GPyTorch models
            parameters.
        
        Returns
        ----------
        pandas.DataFrame
            Domain points for proposed experiments.
        """
        
        # Initialize and train model
        self.model = self.base_model(self.obj.X, 
                                     self.obj.y, 
                                     gpu=self.gpu,
                                     nu=self.nu,
                                     noise_constraint=self.noise_constraint,
                                     lengthscale_prior=self.lengthscale_prior,
                                     outputscale_prior=self.outputscale_prior,
                                     noise_prior=self.noise_prior,
                                     n_restarts=n_restarts,
                                     learning_rate=learning_rate,
                                     training_iters=training_iters
                                     )
        
        self.model.fit()
        
        # Select candidate experiments via acquisition function
        self.proposed_experiments = self.acq.evaluate(self.model, self.obj)
        
        # Append to know results
        if append == True:
            self.obj.get_results(self.proposed_experiments, append=append)
        
        return self.proposed_experiments
        
    # Simulation using known objectives
    def simulate(self, iterations=1, seed=None, fast_comp=False, 
                 update_priors=False, n_restarts=0, learning_rate=0.1,
                 training_iters=100):
        """Run autonomous BO loop.
        
        Run N iterations of optimization with initial results obtained 
        via initialization method and experiments selected from 
        experiment index via the acquisition function. Simulations 
        require know objectives via an index of results or function.
        
        Note
        ----
        Requires a computational objective or experiment index.
        
        Parameters
        ----------
        append : bool
            Append points to results if computational objective or experiment
            index are available.
        n_restarts : int
            Number of restarts used when optimizing GPyTorch model parameters.
        learning_rate : float
            ADAM learning rate used when optimizing GPyTorch model parameters.
        training_iters : int
            Number of iterations to run ADAM when optimizin GPyTorch models
            parameters.
        seed : None, int
            Random seed used for initialization.
        fast_comp : bool, int 
            Use gpytorch fast computation features. Integers specify a 
            threshold number of results above which fast computation will 
            be used.
        update_priors : bool 
            Use parameter estimates from optimization step N-1 as initial 
            values for step N.

        """            
        
        # Initialization data
        self.init_sample(seed=seed, append=True)
        
        # Simulation
        for i in range(iterations):
            
            # Toggle computation
            if fast_comp == True:
                fast_computation(True)
            elif fast_comp == False:
                fast_computation(False)
            elif fast_comp < len(self.obj.y):
                fast_computation(True)
                
            # Use pamater estimates from previous step as initial values
            if update_priors == True and i > 0 and 'GP' in str(self.base_model):
                post_ls = self.model.model.covar_module.base_kernel.lengthscale.detach()[0]
                post_os = self.model.model.covar_module.outputscale.detach()
                post_n = self.model.model.likelihood.noise.detach()[0]
                
                if self.lengthscale_prior == None:
                    self.lengthscale_prior = [None, post_ls]
                else:
                    self.lengthscale_prior[1] = post_ls
                
                if self.outputscale_prior == None:
                    self.outputscale_prior = [None, post_os]
                else:
                    self.outputscale_prior[1] = post_os
                    
                if self.noise_prior == None:
                    self.noise_prior = [None, post_n]
                else:
                    self.noise_prior[1] = post_n
            
            # Initialize and train model
            self.model = self.base_model(self.obj.X, 
                                     self.obj.y, 
                                     gpu=self.gpu,
                                     nu=self.nu,
                                     noise_constraint=self.noise_constraint,
                                     lengthscale_prior=self.lengthscale_prior,
                                     outputscale_prior=self.outputscale_prior,
                                     noise_prior=self.noise_prior,
                                     n_restarts=n_restarts,
                                     learning_rate=learning_rate,
                                     training_iters=training_iters
                                     )
            
            self.model.fit()
        
            # Select candidate experiments via acquisition function
            self.proposed_experiments = self.acq.evaluate(self.model, self.obj)
            
            # Append results to known data
            self.obj.get_results(self.proposed_experiments, append=True)
    
    # Clear results between simulations
    def clear_results(self):
        """Clear results manually. 
        
        Note
        ----
        'rand' and 'pam' initialization methods clear results automatically.
        
        """  
        
        self.obj.clear_results()
    
    # Plot convergence
    def plot_convergence(self, export_path=None):
        """Plot optimizer convergence.
        
        Parameters
        ----------
        export_path : None, str 
            Path to export SVG of optimizer optimizer convergence plot.
        
        Returns
        ----------
        matplotlib.pyplot 
            Plot of optimizer convergence.
        """ 
        
        plot_convergence(
                self.obj.results_input()[self.obj.target],
                self.batch_size,
                export_path=export_path)
    
    # Acquisition summary
    def acquisition_summary(self):
        """Summarize predicted mean and variance for porposed points.
        
        Returns
        ----------
        pandas.DataFrame
            Summary table.
        """
        
        proposed_experiments = self.proposed_experiments.copy()
        X = to_torch(proposed_experiments, gpu=self.gpu)
        
        # Compute mean and variance, then unstandardize
        mean = self.obj.scaler.unstandardize(self.model.predict(X))
        var = (np.sqrt(self.model.variance(X)) * self.obj.scaler.std)**2
        
        # Append to dataframe
        for col, name in zip([mean, var], ['predicted ' + self.obj.target, 'variance']):
            proposed_experiments[name] = col
        
        return proposed_experiments
        
    # Best observed result
    def best(self):
        """Best observed objective values and corresponding domain point."""
        
        sort = self.obj.results_input().sort_values(self.obj.target, ascending=False)
        return sort.head()
    
    
    
    
    
    
    