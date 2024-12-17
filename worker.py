import torch
import torch.multiprocessing as _mp
import numpy as np
from torch.distributions import Categorical
from env import create_train_env
from constants import *
from model import ActorCritic
from logger import MetricLogger

device = 'cpu'
# Worker Process
def worker(global_model, optimizer, global_episode, max_episodes):
    name = _mp.current_process().name
    logger = MetricLogger(LOG_PATH) 
    # print(f"Worker {name} started")
    local_model = ActorCritic(global_model.state_dict()['common.0.weight'].shape[1], global_model.state_dict()['actor.bias'].shape[0]).to(device)
    local_model.load_state_dict(global_model.state_dict())
    # local_model.train()
    env, _, _ = create_train_env()
    local_episode = 0
   
    while global_episode.value < max_episodes:

        local_steps = 0
        state = env.reset()
        log_probs = []
        values = []
        rewards = []
        entropies = []
        local_episode += 1
        #initialize hidden states
        h_0 = torch.zeros((1, 512), dtype=torch.float).to(device)
        c_0 = torch.zeros((1, 512), dtype=torch.float).to(device)
        done = False
        local_model.load_state_dict(global_model.state_dict())
        # Rollout loop
        while not done:
            if local_steps == NUM_LOCAL_STEPS:
                break
            local_steps += 1
            state = torch.tensor(np.array(state), dtype=torch.float32).unsqueeze(0).to(device)
            logits, value, h_0 , c_0 = local_model(state, h_0, c_0) 
            # print("Critic value (before storing):", value)
            action_probs = torch.softmax(logits, dim=-1)
            log_action_probs = torch.log_softmax(logits, dim=1)
            entropy = -(action_probs * log_action_probs).sum(1, keepdim=True)
            m = Categorical(action_probs)
            action = m.sample().item()

            next_state, reward, done, _= env.step(action)
            log_probs.append(log_action_probs[0, action])
            values.append(value)
            rewards.append(reward)
            entropies.append(entropy)

            state = next_state

        # Compute returns and advantages
        
        R = torch.zeros(1, 1).to(device)
        if not done:
            _, R, _, _ = local_model(torch.tensor(np.array(state), dtype=torch.float32).unsqueeze(0).to(device), h_0, c_0)
        gae = torch.zeros(1, 1).to(device)
        actor_loss = 0
        critic_loss = 0
        entropy_loss = 0
        total_reward = sum(rewards)
        next_value = R
      

        for value, reward, log_prob, entropy in list(zip(values, rewards, log_probs, entropies))[::-1]:
            # print("value: ", value)
            # print("reward: ", reward)
            gae = gae * GAMMA * TAU + reward + GAMMA * next_value - value
            next_value = value
            actor_loss += +log_prob * gae
            R = GAMMA * R + reward
            critic_loss += 0.5 * (R - value).pow(2)
            entropy_loss += entropy
        
        total_loss = -actor_loss + critic_loss - BETA * entropy_loss 
        # Backpropagation
        optimizer.zero_grad()
        total_loss.backward()

        for global_param, local_param in zip(global_model.parameters(), local_model.parameters()):

            global_param._grad = local_param.grad

        optimizer.step()

        # Update global episode counter
        with global_episode.get_lock():
            global_episode.value += 1

        if global_episode.value % SAVE_EPISODE_INTERVAL == 0 and global_episode.value > 0:
            torch.save(global_model.state_dict(),
                        "{}/a3c_{}_{}_episode_{}.pt".format(SAVE_PATH, WORLD, STAGE, global_episode.value ))
            
        logger.log_episode(global_episode.value, total_reward, actor_loss.item(), critic_loss.item(), entropy_loss.item())
    
    logger.plot_metrics()