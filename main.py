from configparser import ConfigParser
from argparse import ArgumentParser

import torch
import gym
import numpy as np
import os
import gym_sumo

from agents.ppo import PPO
from agents.sac import SAC
from agents.ddpg import DDPG

from utils.utils import make_transition, Dict, RunningMeanStd
os.makedirs('./model_weights', exist_ok=True)

parser = ArgumentParser('parameters')

parser.add_argument("--env_name", type=str, default ='gym_sumo-v0')
parser.add_argument("--algo", type=str, default = 'ppo', help = 'algorithm to adjust (default : ppo)')
parser.add_argument('--train', type=bool, default=True, help="(default: True)")
parser.add_argument('--render', type=bool, default=False, help="(default: False)")
parser.add_argument('--epochs', type=int, default=1000, help='number of epochs, (default: 1000)')
parser.add_argument('--tensorboard', type=bool, default=False, help='use_tensorboard, (default: False)')
parser.add_argument("--load", type=str, default = 'no', help = 'load network name in ./model_weights')
parser.add_argument("--save_interval", type=int, default = 100, help = 'save interval(default: 100)')
parser.add_argument("--print_interval", type=int, default = 1, help = 'print interval(default : 20)')
parser.add_argument("--use_cuda", type=bool, default = True, help = 'cuda usage(default : True)')
parser.add_argument("--reward_scaling", type=float, default = 0.1, help = 'reward scaling(default : 0.1)')
args = parser.parse_args()
parser = ConfigParser()
parser.read('config.ini')
agent_args = Dict(parser,args.algo)

device = 'cuda' if torch.cuda.is_available() else 'cpu'
if args.use_cuda == False:
    device = 'cpu'
    
if args.tensorboard:
    from torch.utils.tensorboard import SummaryWriter
    writer = SummaryWriter()
else:
    writer = None
    
env = gym.make(args.env_name)
action_dim = 2
state_dim = 37
state_rms = RunningMeanStd(state_dim)
exp_tag='discrte'

if args.algo == 'ppo' :
    agent = PPO(writer, device, state_dim, action_dim, agent_args)
elif args.algo == 'sac' :
    agent = SAC(writer, device, state_dim, action_dim, agent_args)
elif args.algo == 'ddpg' :
    from utils.noise import OUNoise
    noise = OUNoise(action_dim,0)
    agent = DDPG(writer, device, state_dim, action_dim, agent_args, noise)

    
if (torch.cuda.is_available()) and (args.use_cuda):
    agent = agent.cuda()

if args.load != 'no':
    agent.load_state_dict(torch.load("./model_weights/"+args.load))
    
score_lst = []
state_lst = []
avg_scors=[]

if agent_args.on_policy == True:
    score = 0.0
    state = env.reset(gui=False, numVehicles=15)
    # state = np.clip((state_ - state_rms.mean) / (state_rms.var ** 0.5 + 1e-8), -5, 5)
    for n_epi in range(args.epochs):
        for t in range(agent_args.traj_length):
            # print('t',t)
            if args.render:    
                env.render()
            state_lst.append(state)
            mu,sigma = agent.get_action(torch.from_numpy(state).float().to(device))
            dist = torch.distributions.Normal(mu,sigma[0])
            action = dist.sample()
            log_prob = dist.log_prob(action).sum(-1,keepdim = True)
            # print('action',action)
            next_state_, reward_info, done, info = env.step(action.cpu().numpy())
            reward, R_comf, R_eff, R_safe = reward_info
            next_state = np.clip((next_state_ - state_rms.mean) / (state_rms.var ** 0.5 + 1e-8), -5, 5)
            transition = make_transition(state,\
                                         action.cpu().numpy(),\
                                         np.array([reward*args.reward_scaling]),\
                                         next_state,\
                                         np.array([done]),\
                                         log_prob.detach().cpu().numpy()\
                                        )
            agent.put_data(transition) 
            score += reward
            if done:
                env.close()
                state = env.reset(gui=False, numVehicles=15)
                # state = np.clip((state_ - state_rms.mean) / (state_rms.var ** 0.5 + 1e-8), -5, 5)
                score_lst.append(score)
                if args.tensorboard:
                    writer.add_scalar("score/score", score, n_epi)
                score = 0
            else:
                state = next_state
                state_ = next_state_

        agent.train_net(n_epi)
        state_rms.update(np.vstack(state_lst))
        if n_epi%args.print_interval==0 and n_epi!=0:
            print("# of episode :{}, avg score : {:.1f}".format(n_epi, sum(score_lst)/len(score_lst)))
            avg_scors.append(sum(score_lst)/len(score_lst))
            print('avg scores',avg_scors)
            np.save(f'avgscores_{exp_tag}.npy',avg_scors)
            score_lst = []
        if n_epi%args.save_interval==0 and n_epi!=0:
            torch.save(agent.state_dict(),f'./model_weights/agent_{exp_tag}'+str(n_epi))
            
else : # off policy 
    for n_epi in range(args.epochs):
        score = 0.0
        state = env.reset(gui=False, numVehicles=25)
        done = False
        while not done:
            if args.render:    
                env.render()
            action, _ = agent.get_action(torch.from_numpy(state).float().to(device))
            action = action.cpu().detach().numpy()
            next_state, reward, done, info = env.step(action)
            transition = make_transition(state,\
                                         action,\
                                         np.array([reward*args.reward_scaling]),\
                                         next_state,\
                                         np.array([done])\
                                        )
            agent.put_data(transition) 

            state = next_state

            score += reward
            if agent.data.data_idx > agent_args.learn_start_size: 
                agent.train_net(agent_args.batch_size, n_epi)
        score_lst.append(score)
        if args.tensorboard:
            writer.add_scalar("score/score", score, n_epi)
        if n_epi%args.print_interval==0 and n_epi!=0:
            print("# of episode :{}, avg score : {:.1f}".format(n_epi, sum(score_lst)/len(score_lst)))
            score_lst = []
        if n_epi%args.save_interval==0 and n_epi!=0:
            torch.save(agent.state_dict(),'./model_weights/agent_'+str(n_epi))