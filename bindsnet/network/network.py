import tempfile
from typing import Dict, Optional, Type, Iterable

import torch
import threading
from multiprocessing.pool import ThreadPool
import queue

from .monitors import AbstractMonitor
from .nodes import Nodes
from .topology import AbstractConnection
from ..learning.reward import AbstractReward

import os
import time as timeModule

def load(file_name: str, map_location: str = "cpu", learning: bool = None) -> "Network":
    # language=rst
    """
    Loads serialized network object from disk.

    :param file_name: Path to serialized network object on disk.
    :param map_location: One of ``"cpu"`` or ``"cuda"``. Defaults to ``"cpu"``.
    :param learning: Whether to load with learning enabled. Default loads value from
        disk.
    """
    network = torch.load(open(file_name, "rb"), map_location=map_location)
    if learning is not None and "learning" in vars(network):
        network.learning = learning

    return network


class Network(torch.nn.Module):
    # language=rst
    """
    Central object of the ``bindsnet`` package. Responsible for the simulation and
    interaction of nodes and connections.

    **Example:**

    .. code-block:: python

        import torch
        import matplotlib.pyplot as plt

        from bindsnet         import encoding
        from bindsnet.network import Network, nodes, topology, monitors

        network = Network(dt=1.0)  # Instantiates network.

        X = nodes.Input(100)  # Input layer.
        Y = nodes.LIFNodes(100)  # Layer of LIF neurons.
        C = topology.Connection(source=X, target=Y, w=torch.rand(X.n, Y.n))  # Connection from X to Y.

        # Spike monitor objects.
        M1 = monitors.Monitor(obj=X, state_vars=['s'])
        M2 = monitors.Monitor(obj=Y, state_vars=['s'])

        # Add everything to the network object.
        network.add_layer(layer=X, name='X')
        network.add_layer(layer=Y, name='Y')
        network.add_connection(connection=C, source='X', target='Y')
        network.add_monitor(monitor=M1, name='X')
        network.add_monitor(monitor=M2, name='Y')

        # Create Poisson-distributed spike train inputs.
        data = 15 * torch.rand(100)  # Generate random Poisson rates for 100 input neurons.
        train = encoding.poisson(datum=data, time=5000)  # Encode input as 5000ms Poisson spike trains.

        # Simulate network on generated spike trains.
        inputs = {'X' : train}  # Create inputs mapping.
        network.run(inputs=inputs, time=5000)  # Run network simulation.

        # Plot spikes of input and output layers.
        spikes = {'X' : M1.get('s'), 'Y' : M2.get('s')}

        fig, axes = plt.subplots(2, 1, figsize=(12, 7))
        for i, layer in enumerate(spikes):
            axes[i].matshow(spikes[layer], cmap='binary')
            axes[i].set_title('%s spikes' % layer)
            axes[i].set_xlabel('Time'); axes[i].set_ylabel('Index of neuron')
            axes[i].set_xticks(()); axes[i].set_yticks(())
            axes[i].set_aspect('auto')

        plt.tight_layout(); plt.show()
    """

    def __init__(
        self,
        dt: float = 1.0,
        batch_size: int = 1,
        learning: bool = True,
        reward_fn: Optional[Type[AbstractReward]] = None,
        n_threads = 0
    ) -> None:
        # language=rst
        """
        Initializes network object.

        :param dt: Simulation timestep.
        :param batch_size: Mini-batch size.
        :param learning: Whether to allow connection updates. True by default.
        :param reward_fn: Optional class allowing for modification of reward in case of
            reward-modulated learning.
        """
        super().__init__()

        self.dt = dt
        self.batch_size = batch_size

        self.layers = {}
        self.connections = {}
        self.monitors = {}

        self.train(learning)

        if reward_fn is not None:
            self.reward_fn = reward_fn()
        else:
            self.reward_fn = None

        self.n_threads = n_threads
        if n_threads > 0:
            self.response_queue = queue.Queue()
            #self.threadManager = ThreadManager(os.cpu_count())
            self.threadPool = ThreadPool(n_threads)
        

    def add_layer(self, layer: Nodes, name: str) -> None:
        # language=rst
        """
        Adds a layer of nodes to the network.

        :param layer: A subclass of the ``Nodes`` object.
        :param name: Logical name of layer.
        """
        self.layers[name] = layer
        self.add_module(name, layer)

        layer.train(self.learning)
        layer.compute_decays(self.dt)
        layer.set_batch_size(self.batch_size)

    def add_connection(
        self, connection: AbstractConnection, source: str, target: str
    ) -> None:
        # language=rst
        """
        Adds a connection between layers of nodes to the network.

        :param connection: An instance of class ``Connection``.
        :param source: Logical name of the connection's source layer.
        :param target: Logical name of the connection's target layer.
        """
        self.connections[(source, target)] = connection
        self.add_module(source + "_to_" + target, connection)

        connection.dt = self.dt
        connection.train(self.learning)

    def add_monitor(self, monitor: AbstractMonitor, name: str) -> None:
        # language=rst
        """
        Adds a monitor on a network object to the network.

        :param monitor: An instance of class ``Monitor``.
        :param name: Logical name of monitor object.
        """
        self.monitors[name] = monitor
        monitor.network = self
        monitor.dt = self.dt

    def save(self, file_name: str) -> None:
        # language=rst
        """
        Serializes the network object to disk.

        :param file_name: Path to store serialized network object on disk.

        **Example:**

        .. code-block:: python

            import torch
            import matplotlib.pyplot as plt

            from pathlib          import Path
            from bindsnet.network import *
            from bindsnet.network import topology

            # Build simple network.
            network = Network(dt=1.0)

            X = nodes.Input(100)  # Input layer.
            Y = nodes.LIFNodes(100)  # Layer of LIF neurons.
            C = topology.Connection(source=X, target=Y, w=torch.rand(X.n, Y.n))  # Connection from X to Y.

            # Add everything to the network object.
            network.add_layer(layer=X, name='X')
            network.add_layer(layer=Y, name='Y')
            network.add_connection(connection=C, source='X', target='Y')

            # Save the network to disk.
            network.save(str(Path.home()) + '/network.pt')
        """
        torch.save(self, open(file_name, "wb"))

    def clone(self) -> "Network":
        # language=rst
        """
        Returns a cloned network object.

        :return: A copy of this network.
        """
        virtual_file = tempfile.SpooledTemporaryFile()
        torch.save(self, virtual_file)
        virtual_file.seek(0)
        return torch.load(virtual_file)

    def _get_inputs(self, layers: Iterable = None) -> Dict[str, torch.Tensor]:
        # language=rst
        """
        Fetches outputs from network layers to use as input to downstream layers.

        :param layers: Layers to update inputs for. Defaults to all network layers.
        :return: Inputs to all layers for the current iteration.
        """
        inputs = {}

        if layers is None:
            layers = self.layers

        # Loop over network connections.
        for c in self.connections:
            if c[1] in layers:
                # Fetch source and target populations.
                source = self.connections[c].source
                target = self.connections[c].target

                if not c[1] in inputs:
                    inputs[c[1]] = torch.zeros(
                        self.batch_size, *target.shape, device=target.s.device
                    )

                # Add to input: source's spikes multiplied by connection weights.
                inputs[c[1]] += self.connections[c].compute(source.s)

        return inputs

    def run(
        self, inputs: Dict[str, torch.Tensor], time: int, one_step=False, **kwargs
    ) -> None:
        # language=rst
        """
        Simulate network for given inputs and time.

        :param inputs: Dictionary of ``Tensor``s of shape ``[time, *input_shape]`` or
                      ``[time, batch_size, *input_shape]``.
        :param time: Simulation time.
        :param one_step: Whether to run the network in "feed-forward" mode, where inputs
            propagate all the way through the network in a single simulation time step.
            Layers are updated in the order they are added to the network.

        Keyword arguments:

        :param Dict[str, torch.Tensor] clamp: Mapping of layer names to boolean masks if
            neurons should be clamped to spiking. The ``Tensor``s have shape
            ``[n_neurons]`` or ``[time, n_neurons]``.
        :param Dict[str, torch.Tensor] unclamp: Mapping of layer names to boolean masks
            if neurons should be clamped to not spiking. The ``Tensor``s should have
            shape ``[n_neurons]`` or ``[time, n_neurons]``.
        :param Dict[str, torch.Tensor] injects_v: Mapping of layer names to boolean
            masks if neurons should be added voltage. The ``Tensor``s should have shape
            ``[n_neurons]`` or ``[time, n_neurons]``.
        :param Union[float, torch.Tensor] reward: Scalar value used in reward-modulated
            learning.
        :param Dict[Tuple[str], torch.Tensor] masks: Mapping of connection names to
            boolean masks determining which weights to clamp to zero.

        **Example:**

        .. code-block:: python

            import torch
            import matplotlib.pyplot as plt

            from bindsnet.network import Network
            from bindsnet.network.nodes import Input
            from bindsnet.network.monitors import Monitor

            # Build simple network.
            network = Network()
            network.add_layer(Input(500), name='I')
            network.add_monitor(Monitor(network.layers['I'], state_vars=['s']), 'I')

            # Generate spikes by running Bernoulli trials on Uniform(0, 0.5) samples.
            spikes = torch.bernoulli(0.5 * torch.rand(500, 500))

            # Run network simulation.
            network.run(inputs={'I' : spikes}, time=500)

            # Look at input spiking activity.
            spikes = network.monitors['I'].get('s')
            plt.matshow(spikes, cmap='binary')
            plt.xticks(()); plt.yticks(());
            plt.xlabel('Time'); plt.ylabel('Neuron index')
            plt.title('Input spiking')
            plt.show()
        """
        # Parse keyword arguments.
        clamps = kwargs.get("clamp", {})
        unclamps = kwargs.get("unclamp", {})
        masks = kwargs.get("masks", {})
        injects_v = kwargs.get("injects_v", {})

        # Compute reward.
        if self.reward_fn is not None:
            kwargs["reward"] = self.reward_fn.compute(**kwargs)

        # Dynamic setting of batch size.
        if inputs != {}:
            for key in inputs:
                # goal shape is [time, batch, n_0, ...]
                if len(inputs[key].size()) == 1:
                    # current shape is [n_0, ...]
                    # unsqueeze twice to make [1, 1, n_0, ...]
                    inputs[key] = inputs[key].unsqueeze(0).unsqueeze(0)
                elif len(inputs[key].size()) == 2:
                    # current shape is [time, n_0, ...]
                    # unsqueeze dim 1 so that we have
                    # [time, 1, n_0, ...]
                    inputs[key] = inputs[key].unsqueeze(1)

            for key in inputs:
                # batch dimension is 1, grab this and use for batch size
                if inputs[key].size(1) != self.batch_size:
                    self.batch_size = inputs[key].size(1)

                    for l in self.layers:
                        self.layers[l].set_batch_size(self.batch_size)

                    for m in self.monitors:
                        self.monitors[m].reset_state_variables()

                break

        # Effective number of timesteps.
        timesteps = int(time / self.dt)

        # Simulate network activity for `time` timesteps.
        for t in range(timesteps):
            # Get input to all layers (synchronous mode).
            current_inputs = {}
            if not one_step:
                if self.n_threads == 0:
                    current_inputs.update(self._get_inputs())
                else:
                    current_inputs.update(self._get_inputs_threadManager())
            
            for l in self.layers:
                # Update each layer of nodes.
                if l in inputs:
                    if l in current_inputs:
                        current_inputs[l] += inputs[l][t]
                    else:
                        current_inputs[l] = inputs[l][t]

                if one_step:
                    # Get input to this layer (one-step mode).
                    current_inputs.update(self._get_inputs(layers=[l]))

                if l in current_inputs:
                    if self.n_threads == 0:
                        self.layers[l].forward(x=current_inputs[l])
                    else:
                        self.threadManager.q0.put({"type":"forward","items":(self.layers[l],current_inputs[l])})
                else:
                    if self.n_threads == 0:
                        self.layers[l].forward(x=torch.zeros(self.layers[l].s.shape))
                    else:
                        self.threadManager.q0.put({"type":"forward","items":(self.layers[l],torch.zeros(self.layers[l].s.shape))})

                if self.n_threads != 0:
                    self.threadManager.q0.join()

                # Clamp neurons to spike.
                clamp = clamps.get(l, None)
                if clamp is not None:
                    if clamp.ndimension() == 1:
                        self.layers[l].s[:, clamp] = 1
                    else:
                        self.layers[l].s[:, clamp[t]] = 1

                # Clamp neurons not to spike.
                unclamp = unclamps.get(l, None)
                if unclamp is not None:
                    if unclamp.ndimension() == 1:
                        self.layers[l].s[:, unclamp] = 0
                    else:
                        self.layers[l].s[:, unclamp[t]] = 0

                # Inject voltage to neurons.
                inject_v = injects_v.get(l, None)
                if inject_v is not None:
                    if inject_v.ndimension() == 1:
                        self.layers[l].v += inject_v
                    else:
                        self.layers[l].v += inject_v[t]

            # Run synapse updates.
            for c in self.connections:
                if self.n_threads == 0:
                    self.connections[c].update(
                        mask=masks.get(c, None), learning=self.learning, **kwargs
                    )
                else:
                    self.threadManager.q0.put({"type":"connectionUpdate","items":(self.connections[c],masks.get(c, None),self.learning)})

            if self.n_threads != 0:
                self.threadManager.q0.join()

            # Get input to all layers.
            # OYS 11/28/20 is this necessary? Seems like it gets negated upon the next loop
            #current_inputs.update(self._get_inputs())

            # Record state variables of interest.
            for m in self.monitors:
                self.monitors[m].record()

        # Re-normalize connections.
        for c in self.connections:
            self.connections[c].normalize()

    def reset_state_variables(self) -> None:
        # language=rst
        """
        Reset state variables of objects in network.
        """
        for layer in self.layers:
            self.layers[layer].reset_state_variables()

        for connection in self.connections:
            self.connections[connection].reset_state_variables()

        for monitor in self.monitors:
            self.monitors[monitor].reset_state_variables()

    def train(self, mode: bool = True) -> "torch.nn.Module":
        # language=rst
        """
        Sets the node in training mode.

        :param mode: Turn training on or off.

        :return: ``self`` as specified in ``torch.nn.Module``.
        """
        self.learning = mode
        return super().train(mode)

    def wait_for_next_thread(self,threads,n_threads):
        while len(threads) > n_threads:
            threads[0].join()
            del threads[0]

    def runThreaded(
        self, inputs: Dict[str, torch.Tensor], time: int, one_step=False, n_threads=1, **kwargs
    ) -> None:
        # language=rst
        """
        Simulate network for given inputs and time.

        :param inputs: Dictionary of ``Tensor``s of shape ``[time, *input_shape]`` or
                      ``[time, batch_size, *input_shape]``.
        :param time: Simulation time.
        :param one_step: Whether to run the network in "feed-forward" mode, where inputs
            propagate all the way through the network in a single simulation time step.
            Layers are updated in the order they are added to the network.

        Keyword arguments:

        :param Dict[str, torch.Tensor] clamp: Mapping of layer names to boolean masks if
            neurons should be clamped to spiking. The ``Tensor``s have shape
            ``[n_neurons]`` or ``[time, n_neurons]``.
        :param Dict[str, torch.Tensor] unclamp: Mapping of layer names to boolean masks
            if neurons should be clamped to not spiking. The ``Tensor``s should have
            shape ``[n_neurons]`` or ``[time, n_neurons]``.
        :param Dict[str, torch.Tensor] injects_v: Mapping of layer names to boolean
            masks if neurons should be added voltage. The ``Tensor``s should have shape
            ``[n_neurons]`` or ``[time, n_neurons]``.
        :param Union[float, torch.Tensor] reward: Scalar value used in reward-modulated
            learning.
        :param Dict[Tuple[str], torch.Tensor] masks: Mapping of connection names to
            boolean masks determining which weights to clamp to zero.

        **Example:**

        .. code-block:: python

            import torch
            import matplotlib.pyplot as plt

            from bindsnet.network import Network
            from bindsnet.network.nodes import Input
            from bindsnet.network.monitors import Monitor

            # Build simple network.
            network = Network()
            network.add_layer(Input(500), name='I')
            network.add_monitor(Monitor(network.layers['I'], state_vars=['s']), 'I')

            # Generate spikes by running Bernoulli trials on Uniform(0, 0.5) samples.
            spikes = torch.bernoulli(0.5 * torch.rand(500, 500))

            # Run network simulation.
            network.run(inputs={'I' : spikes}, time=500)

            # Look at input spiking activity.
            spikes = network.monitors['I'].get('s')
            plt.matshow(spikes, cmap='binary')
            plt.xticks(()); plt.yticks(());
            plt.xlabel('Time'); plt.ylabel('Neuron index')
            plt.title('Input spiking')
            plt.show()
        """

        # Parse keyword arguments.
        clamps = kwargs.get("clamp", {})
        unclamps = kwargs.get("unclamp", {})
        masks = kwargs.get("masks", {})
        injects_v = kwargs.get("injects_v", {})

        # Compute reward.
        if self.reward_fn is not None:
            kwargs["reward"] = self.reward_fn.compute(**kwargs)

        # Dynamic setting of batch size.
        if inputs != {}:
            for key in inputs:
                # goal shape is [time, batch, n_0, ...]
                if len(inputs[key].size()) == 1:
                    # current shape is [n_0, ...]
                    # unsqueeze twice to make [1, 1, n_0, ...]
                    inputs[key] = inputs[key].unsqueeze(0).unsqueeze(0)
                elif len(inputs[key].size()) == 2:
                    # current shape is [time, n_0, ...]
                    # unsqueeze dim 1 so that we have
                    # [time, 1, n_0, ...]
                    inputs[key] = inputs[key].unsqueeze(1)

            for key in inputs:
                # batch dimension is 1, grab this and use for batch size
                if inputs[key].size(1) != self.batch_size:
                    self.batch_size = inputs[key].size(1)

                    for l in self.layers:
                        self.layers[l].set_batch_size(self.batch_size)

                    for m in self.monitors:
                        self.monitors[m].reset_state_variables()

                break

        # Effective number of timesteps.
        timesteps = int(time / self.dt)

        # Simulate network activity for `time` timesteps.
        for t in range(timesteps):
            # Get input to all layers (synchronous mode).
            current_inputs = {}
            if not one_step:
                current_inputs.update(self._get_inputs())

            # run node layer updates
            threads = []
            for l in self.layers:
                if l in inputs:
                    if l in current_inputs:
                        current_inputs[l] += inputs[l][t]
                    else:
                        current_inputs[l] = inputs[l][t]

                if one_step:
                    # Get input to this layer (one-step mode).
                    current_inputs.update(self._get_inputs(layers=[l]))

                layer_inputs = current_inputs[l] if (l in current_inputs) else torch.zeros(self.layers[l].s.shape)
                self.wait_for_next_thread(threads,n_threads)
                t = threading.Thread(target=self._layer_evaluation,args=(l,self.layers[l],layer_inputs,t,injects_v,clamps,unclamps,))
                t.start()
                threads.append(t)
            
            for t in threads:
                t.join()

            # Run synapse updates.
            threads = []
            for c in self.connections:
                self.wait_for_next_thread(threads,n_threads)
                t = threading.Thread(target=self._connection_update,args=(self.connections[c],masks,kwargs,)) 
                t.start()
                threads.append(t)
            
            for t in threads:
                t.join()

            # Get input to all layers.
            # OYS 11/28/20 is this necessary? Seems like it gets negated upon the next loop
            current_inputs.update(self._get_inputs())

            # Record state variables of interest.
            threads = []
            for m in self.monitors:
                self.wait_for_next_thread(threads,n_threads)
                t = threading.Thread(target=self._monitor_record,args=(self.monitors[m],)) 
                t.start()
                threads.append(t)
            
            for t in threads:
                t.join()
            
        # Re-normalize connections.
        threads = []
        for c in self.connections:
            self.wait_for_next_thread(threads,n_threads)
            t = threading.Thread(target=self._connection_normalize,args=(self.connections[c],))
            t.start()
            threads.append(t)
        
        for t in threads:
            t.join()
            

    def _connection_update(self,c,masks,kwargs):
        c.update(
            mask=masks.get(c, None), learning=self.learning, **kwargs
        )
        self.response_queue.put(True)

    def _monitor_record(self,m):
        m.record()
        self.response_queue.put(True)

    def _connection_normalize(self,c):
        c.normalize()
        self.response_queue.put(True)

    def _layer_evaluation(self,l,layer,inputs,t,injects_v,clamps,unclamps):
        print("Thread workig on layer",l)
        # Update each layer of nodes.
        layer.forward(x=inputs)

        # Clamp neurons to spike.
        clamp = clamps.get(l, None)
        if clamp is not None:
            if clamp.ndimension() == 1:
                layer.s[:, clamp] = 1
            else:
                layer.s[:, clamp[t]] = 1

        # Clamp neurons not to spike.
        unclamp = unclamps.get(l, None)
        if unclamp is not None:
            if unclamp.ndimension() == 1:
                layer.s[:, unclamp] = 0
            else:
                layer.s[:, unclamp[t]] = 0

        # Inject voltage to neurons.
        inject_v = injects_v.get(l, None)
        if inject_v is not None:
            if inject_v.ndimension() == 1:
                layer.v += inject_v
            else:
                layer.v += inject_v[t]

        print("Layer done:",l)
        self.response_queue.put(True)

    def runThreadPool(
        self, inputs: Dict[str, torch.Tensor], time: int, one_step=False, **kwargs
    ) -> None:
        # language=rst
        """
        Simulate network for given inputs and time.

        :param inputs: Dictionary of ``Tensor``s of shape ``[time, *input_shape]`` or
                      ``[time, batch_size, *input_shape]``.
        :param time: Simulation time.
        :param one_step: Whether to run the network in "feed-forward" mode, where inputs
            propagate all the way through the network in a single simulation time step.
            Layers are updated in the order they are added to the network.

        Keyword arguments:

        :param Dict[str, torch.Tensor] clamp: Mapping of layer names to boolean masks if
            neurons should be clamped to spiking. The ``Tensor``s have shape
            ``[n_neurons]`` or ``[time, n_neurons]``.
        :param Dict[str, torch.Tensor] unclamp: Mapping of layer names to boolean masks
            if neurons should be clamped to not spiking. The ``Tensor``s should have
            shape ``[n_neurons]`` or ``[time, n_neurons]``.
        :param Dict[str, torch.Tensor] injects_v: Mapping of layer names to boolean
            masks if neurons should be added voltage. The ``Tensor``s should have shape
            ``[n_neurons]`` or ``[time, n_neurons]``.
        :param Union[float, torch.Tensor] reward: Scalar value used in reward-modulated
            learning.
        :param Dict[Tuple[str], torch.Tensor] masks: Mapping of connection names to
            boolean masks determining which weights to clamp to zero.

        **Example:**

        .. code-block:: python

            import torch
            import matplotlib.pyplot as plt

            from bindsnet.network import Network
            from bindsnet.network.nodes import Input
            from bindsnet.network.monitors import Monitor

            # Build simple network.
            network = Network()
            network.add_layer(Input(500), name='I')
            network.add_monitor(Monitor(network.layers['I'], state_vars=['s']), 'I')

            # Generate spikes by running Bernoulli trials on Uniform(0, 0.5) samples.
            spikes = torch.bernoulli(0.5 * torch.rand(500, 500))

            # Run network simulation.
            network.run(inputs={'I' : spikes}, time=500)

            # Look at input spiking activity.
            spikes = network.monitors['I'].get('s')
            plt.matshow(spikes, cmap='binary')
            plt.xticks(()); plt.yticks(());
            plt.xlabel('Time'); plt.ylabel('Neuron index')
            plt.title('Input spiking')
            plt.show()
        """
        # Parse keyword arguments.
        clamps = kwargs.get("clamp", {})
        unclamps = kwargs.get("unclamp", {})
        masks = kwargs.get("masks", {})
        injects_v = kwargs.get("injects_v", {})

        # Compute reward.
        if self.reward_fn is not None:
            kwargs["reward"] = self.reward_fn.compute(**kwargs)

        # Dynamic setting of batch size.
        if inputs != {}:
            for key in inputs:
                # goal shape is [time, batch, n_0, ...]
                if len(inputs[key].size()) == 1:
                    # current shape is [n_0, ...]
                    # unsqueeze twice to make [1, 1, n_0, ...]
                    inputs[key] = inputs[key].unsqueeze(0).unsqueeze(0)
                elif len(inputs[key].size()) == 2:
                    # current shape is [time, n_0, ...]
                    # unsqueeze dim 1 so that we have
                    # [time, 1, n_0, ...]
                    inputs[key] = inputs[key].unsqueeze(1)

            for key in inputs:
                # batch dimension is 1, grab this and use for batch size
                if inputs[key].size(1) != self.batch_size:
                    self.batch_size = inputs[key].size(1)

                    for l in self.layers:
                        self.layers[l].set_batch_size(self.batch_size)

                    for m in self.monitors:
                        self.monitors[m].reset_state_variables()

                break

        # Effective number of timesteps.
        timesteps = int(time / self.dt)

        # Simulate network activity for `time` timesteps.
        for t in range(timesteps):
            # Get input to all layers (synchronous mode).
            current_inputs = {}
            if not one_step:
                current_inputs.update(self._get_inputs())

            # run node layer updates
            for l in self.layers:
                if l in inputs:
                    if l in current_inputs:
                        current_inputs[l] += inputs[l][t]
                    else:
                        current_inputs[l] = inputs[l][t]

                if one_step:
                    # Get input to this layer (one-step mode).
                    current_inputs.update(self._get_inputs(layers=[l]))

                layer_inputs = current_inputs[l] if (l in current_inputs) else torch.zeros(self.layers[l].s.shape)
                self.threadPool.apply_async(self._layer_evaluation,args=(l,self.layers[l],layer_inputs,t,injects_v,clamps,unclamps,))
            
            for l in self.layers:
                self.response_queue.get()
                self.response_queue.task_done()

            # Run synapse updates.
            for c in self.connections:
                self.threadPool.apply_async(self._connection_update,args=(self.connections[c],masks,kwargs,))
            
            for c in self.connections:
                self.response_queue.get()
                self.response_queue.task_done()

            # Get input to all layers.
            # OYS 11/28/20 is this necessary? Seems like it gets negated upon the next loop
            # current_inputs.update(self._get_inputs())

            # Record state variables of interest.
            for m in self.monitors:
                self.threadPool.apply_async(self._monitor_record,args=(self.monitors[m],))
            
            for m in self.monitors:
                self.response_queue.get()
                self.response_queue.task_done()

        # Re-normalize connections.
        for c in self.connections:
            self.connections[c].normalize()

    def runThreadManager(
        self, inputs: Dict[str, torch.Tensor], time: int, one_step=False, **kwargs
    ) -> None:
        # language=rst
        """
        Simulate network for given inputs and time.

        :param inputs: Dictionary of ``Tensor``s of shape ``[time, *input_shape]`` or
                      ``[time, batch_size, *input_shape]``.
        :param time: Simulation time.
        :param one_step: Whether to run the network in "feed-forward" mode, where inputs
            propagate all the way through the network in a single simulation time step.
            Layers are updated in the order they are added to the network.

        Keyword arguments:

        :param Dict[str, torch.Tensor] clamp: Mapping of layer names to boolean masks if
            neurons should be clamped to spiking. The ``Tensor``s have shape
            ``[n_neurons]`` or ``[time, n_neurons]``.
        :param Dict[str, torch.Tensor] unclamp: Mapping of layer names to boolean masks
            if neurons should be clamped to not spiking. The ``Tensor``s should have
            shape ``[n_neurons]`` or ``[time, n_neurons]``.
        :param Dict[str, torch.Tensor] injects_v: Mapping of layer names to boolean
            masks if neurons should be added voltage. The ``Tensor``s should have shape
            ``[n_neurons]`` or ``[time, n_neurons]``.
        :param Union[float, torch.Tensor] reward: Scalar value used in reward-modulated
            learning.
        :param Dict[Tuple[str], torch.Tensor] masks: Mapping of connection names to
            boolean masks determining which weights to clamp to zero.

        **Example:**

        .. code-block:: python

            import torch
            import matplotlib.pyplot as plt

            from bindsnet.network import Network
            from bindsnet.network.nodes import Input
            from bindsnet.network.monitors import Monitor

            # Build simple network.
            network = Network()
            network.add_layer(Input(500), name='I')
            network.add_monitor(Monitor(network.layers['I'], state_vars=['s']), 'I')

            # Generate spikes by running Bernoulli trials on Uniform(0, 0.5) samples.
            spikes = torch.bernoulli(0.5 * torch.rand(500, 500))

            # Run network simulation.
            network.run(inputs={'I' : spikes}, time=500)

            # Look at input spiking activity.
            spikes = network.monitors['I'].get('s')
            plt.matshow(spikes, cmap='binary')
            plt.xticks(()); plt.yticks(());
            plt.xlabel('Time'); plt.ylabel('Neuron index')
            plt.title('Input spiking')
            plt.show()
        """
        # Parse keyword arguments.
        clamps = kwargs.get("clamp", {})
        unclamps = kwargs.get("unclamp", {})
        masks = kwargs.get("masks", {})
        injects_v = kwargs.get("injects_v", {})

        # Compute reward.
        if self.reward_fn is not None:
            kwargs["reward"] = self.reward_fn.compute(**kwargs)

        # Dynamic setting of batch size.
        if inputs != {}:
            for key in inputs:
                # goal shape is [time, batch, n_0, ...]
                if len(inputs[key].size()) == 1:
                    # current shape is [n_0, ...]
                    # unsqueeze twice to make [1, 1, n_0, ...]
                    inputs[key] = inputs[key].unsqueeze(0).unsqueeze(0)
                elif len(inputs[key].size()) == 2:
                    # current shape is [time, n_0, ...]
                    # unsqueeze dim 1 so that we have
                    # [time, 1, n_0, ...]
                    inputs[key] = inputs[key].unsqueeze(1)

            for key in inputs:
                # batch dimension is 1, grab this and use for batch size
                if inputs[key].size(1) != self.batch_size:
                    self.batch_size = inputs[key].size(1)

                    for l in self.layers:
                        self.layers[l].set_batch_size(self.batch_size)

                    for m in self.monitors:
                        self.monitors[m].reset_state_variables()

                break

        # Effective number of timesteps.
        timesteps = int(time / self.dt)

        # Simulate network activity for `time` timesteps.
        for t in range(timesteps):
            # Get input to all layers (synchronous mode).
            current_inputs = {}
            if not one_step:
                current_inputs.update(self._get_inputs_threadManager())
            
            for l in self.layers:
                # Update each layer of nodes.
                if l in inputs:
                    if l in current_inputs:
                        current_inputs[l] += inputs[l][t]
                    else:
                        current_inputs[l] = inputs[l][t]

                if one_step:
                    # Get input to this layer (one-step mode).
                    current_inputs.update(self._get_inputs(layers=[l]))

                if l in current_inputs:
                    self.threadManager.q0.put({"type":"forward","items":(self.layers[l],current_inputs[l])})
                else:
                    self.threadManager.q0.put({"type":"forward","items":(self.layers[l],torch.zeros(self.layers[l].s.shape))})

            self.threadManager.q0.join()

            for l in self.layers:
                # Clamp neurons to spike.
                clamp = clamps.get(l, None)
                if clamp is not None:
                    if clamp.ndimension() == 1:
                        self.layers[l].s[:, clamp] = 1
                    else:
                        self.layers[l].s[:, clamp[t]] = 1

                # Clamp neurons not to spike.
                unclamp = unclamps.get(l, None)
                if unclamp is not None:
                    if unclamp.ndimension() == 1:
                        self.layers[l].s[:, unclamp] = 0
                    else:
                        self.layers[l].s[:, unclamp[t]] = 0

                # Inject voltage to neurons.
                inject_v = injects_v.get(l, None)
                if inject_v is not None:
                    if inject_v.ndimension() == 1:
                        self.layers[l].v += inject_v
                    else:
                        self.layers[l].v += inject_v[t]

            # Run synapse updates.
            for c in self.connections:
                self.threadManager.q0.put({"type":"connectionUpdate","items":(self.connections[c],masks.get(c, None),self.learning)})
            
            self.threadManager.q0.join()

            # Record state variables of interest.
            for m in self.monitors:
                self.monitors[m].record()

        # Re-normalize connections.
        for c in self.connections:
            self.connections[c].normalize()

    def _get_inputs_threadPool(self, layers: Iterable = None) -> Dict[str, torch.Tensor]:
        # language=rst
        """
        Fetches outputs from network layers to use as input to downstream layers.

        :param layers: Layers to update inputs for. Defaults to all network layers.
        :return: Inputs to all layers for the current iteration.
        """
        inputs = {}

        if layers is None:
            layers = self.layers

        # Loop over network connections.
        for c in self.connections:
            if c[1] in layers:
                # Fetch source and target populations.
                source = self.connections[c].source
                target = self.connections[c].target

                if not c[1] in inputs:
                    inputs[c[1]] = torch.zeros(
                        self.batch_size, *target.shape, device=target.s.device
                    )

                # Add to input: source's spikes multiplied by connection weights.
                self.threadPool.apply_async(self.connections[c].compute,args=(source.s,None,self.response_queue,))

        for c in self.connections:
            if c[1] in layers:
                self.response_queue.get()

        return inputs

    def _get_inputs_threadManager(self, layers: Iterable = None) -> Dict[str, torch.Tensor]:
        # language=rst
        """
        Fetches outputs from network layers to use as input to downstream layers.

        :param layers: Layers to update inputs for. Defaults to all network layers.
        :return: Inputs to all layers for the current iteration.
        """
        inputs = {}

        if layers is None:
            layers = self.layers

        # Loop over network connections.
        for c in self.connections:
            if c[1] in layers:
                # Fetch source and target populations.
                source = self.connections[c].source
                target = self.connections[c].target

                if not c[1] in inputs:
                    inputs[c[1]] = torch.zeros(
                        self.batch_size, *target.shape, device=target.s.device
                    )


                # Add to input: source's spikes multiplied by connection weights.
                inputs[c[1]] += self.connections[c].compute(source.s,self.threadManager)

        return inputs



# Custom class to assign tasks to threads
class ThreadManager:
    def __init__(self,n_threads):

        # number of threads to be used
        self.n_threads = n_threads

        # the job queue
        self.q0 = queue.Queue()

        # the response queue
        self.q1 = queue.Queue()

        # keep track of threads in a list
        self.threads = []
        for _ in range(n_threads):
            # create and start the threads
            t = threading.Thread(target=self._threadCompute,daemon=True)
            t.start()
            self.threads.append(t)

    # Thread Manager threads worker logic
    def _threadCompute(self):
        while True:
            # get a task from the job queue
            task = self.q0.get()

            # get the task type and data
            taskType = task["type"]
            items = task["items"]

            # thread should compute the synapse outputs
            if taskType == "computeInputs":
                # collect the data and perform matrix multiplication on the partial tensor
                start_idx = int(items[0][0])
                end_idx = int(items[0][1])
                s = items[1]
                w = items[2]
                b = items[3]
                t_spikes  = s
                t_weights = w[:,start_idx:end_idx]
                result = t_spikes @ t_weights + b[start_idx:end_idx]
                returnVal = ((start_idx,end_idx),result)

                # store the results in the response queue for the Connection object to reuse
                self.q1.put(returnVal)
                self.q0.task_done()

            # thread should compute the layer output
            elif taskType == "forward":
                # collect data and call the layer object's forward method
                layer = items[0]
                inputs = items[1]
                layer.forward(inputs)
                self.q0.task_done()

            # thread should update the synapse weights
            elif taskType == "connectionUpdate":
                # collect data and call the connection object's update method
                c = items[0]
                mask = items[1]
                learning = items[2]
                c.update( mask=mask, learning=learning)
                self.q0.task_done()