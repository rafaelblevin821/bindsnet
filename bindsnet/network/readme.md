# Requirements
### Source Code Requirements
* Python 3.6
* PyTorch
* BindsNET (https://github.com/oshears/bindsnet)

### Optional Requirements
* cProfile
* snakeviz

# BindsNET Changes Overview
To add multithreading to BindsNET, we modified the `network.py` and `topology.py` files. These modifications are explained in the following sections.

# `network.py`

## \_\_init\_\_()
The `Network` object's constructor method was modified to allow users to pass in the number of threads (`n_threads`) they would like to use with the network. If the user specified a number greater than 0, a `ThreadPool` (or `ThreadManager`) and `Queue` object will be created to allow synchronization between the threads.

## Threaded Methods

### _get_inputs_threadManager()
This is an multithreaded version of the _get_inputs() method that uses the `ThreadManager` to organize the threads. The standard _get_inputs() method runs the `compute()` method of each `Connection` object, while this version sends the connection objects to its threads to perform the `compute()` methods. The tasks are submitted using `self.threadManager.q0.put()`.


### _get_inputsThreadPool()
This is an multithreaded version of the _get_inputs() method that uses the `ThreadPool` to organize the threads. The standard _get_inputs() method runs the `compute()` method of each `Connection` object, while this version sends the connection objects to its threads to perform the `compute()` methods. The tasks are submitted using `self.threadPool.apply()`.

### _connection_update()
This is a threaded method that performs `update()` on the `Connection` object that is passed to it. After completing this task, it places a value into `response_queue` to let the `ThreadPool` know that the task has been completed.

### _monitor_record()
This is a threaded method that performs `record()` on the `Monitor` object that is passed to it. A network can have multiple monitor objects to keep track of the states of each neuron layer (`Node` object). After completing this task, it places a value into `response_queue` to let the `ThreadPool` know that the task has been completed.

### _connection_normalize()
This is a threaded method that performs `normalize()` on the `Connection` object that is passed to it. After completing this task, it places a value into `response_queue` to let the `ThreadPool` know that the task has been completed.

### _layer_evaluation()
This is a threaded method that performs `forward()` on the `Nodes` object that is passed to it. After completing this task, it places a value into `response_queue` to let the `ThreadPool` know that the task has been completed.

## Run Methods

### run()
The standard run method for the SNN was adapted to run the normal method if no threads were specified, or to use the `ThreadManager` object if `n_threads` was set to a value greater than 0. For running threaded methods, a new task would be placed into the `ThreadManager`'s `q0` object. The main thread would then be required to wait for the worker threads to finish their tasks by calling `threadManager.q0.join()`. Worker threads indicate that they have finished their task by calling `q0.task_done()` on `ThreadManager`'s `q0`.

### runThreaded()
This method is an adaptation of the standard `run()` method that create a new `Thread` object for each multithreaded operation. Threads in this method are setup to run the interal threaded methods (i.e., `_connection_update()`, `_connection_normalize()`, `_layer_evaluation()`)

In addition, if the network attempts to kick off more threads than what was specified, the `wait_for_next_thread()` method blocks until at least one threads has completed its task. This prevents the network from spawning more than `n_threads`.

### runThreadPool()
This method is an adaptation of the standard `run()` method that uses the `ThreadPool` object to manage the mutlithreaded operations. Tasks are submitted to the `ThreadPool` using `self.threadPool.apply_async()`, where the function argument is one of the aforementioned "Threaded Methods". The main thread is blocked by waiting on the `self.response_queue` to return all of the tasks sent to the `ThreadPool` .


### runThreadManager()
This method is an adaptation of the standard `run()` method that uses the `ThreadManager` object to manage the multithreaded operations. Tasks are submitted to `Thread` objects in the `ThreadManager` using the `ThreadManager`'s `q0` object. A task is created as a dictionary with its type and arguments as values.

## ThreadManager
This is one class used to benchmark the multithreaded execution of the SNN. `ThreadManager` constructs `n_threads` threads upon creation, in addition to two `Queue` objects to allow threads to recieve tasks and return response information.

### _threadCompute()
This is the method that worker threads created by `ThreadManager` execute. Worker threads accepted three types of tasks:
* `computeInputs` which is used to produce synapse (`Connection` object) outputs
* `connectionUpdate` which is used to update the weights are each synapse (`Connection` object)
* `forward` which is used to update the outputs at each layer (`Nodes` object) in the network

Tasks are submitted to the worker threads via the `ThreadManager`'s `q0` object. The threads may return information using the `q1` object.

# `topology.py`

## Connection
The `Connection` object was slightly modified to work with the `Network`'s `ThreadManager` and `ThreadPool` objects. If a `ThreadManager` is specified, the connection will submit tasks using `threadManager.q0.put()`. This splits up the matrix multiplication task. Otherwise if a `response_queue` is specified, the connection will assume that a thread is executing its `compute()` method and place a value in `response_queue` to indicate that it has completed the task.

# `snn_benchmark.py`
# `hogwild_snn_benchmark.py`
# `CNN_test.py`
This file is not directly used; however, this is part of the test process for the CNN. This python file contains two methods, the one needed to test the accurasy of the trained architecture is the `test`method.

## test_epoch()
This is the method that tests a single epoch of the CNN. It is responsible for the performing the forward propagation and calculating the accurasy of the epoch.

## test()
A wrapper method which loads the test dataset and then calls the `test_epoch`

# `CNN_train.py`
This file contains the methods used for training the CNN. The main method needed for training is the `train` method.

## train_epoch()
This is the method that trains a single epoch of the CNN. It is responsible for performing both the forward and back propagation then prints the status of the CNN after these operations have been performed.

## train()
This is a wrapper method which loads the training dataset and then calls the `train_epoch` method.


# `CNN_main.py`
This is the main python file used to benchmark the MNIST dataset. Currently, this only benchmarks the training as this was the focus of the experiment. This file defines the CNN architecture and determines if only the CPUs or CUDA (GPU) and CPUs are utilized. This is where the multiprocessing functionality has been added. The file will output the execution time for all 6000 MNIST examples to be trained. Because one MNIST example translates to one complete forward and back propagation, the throughput is calculated via `execution time for all training sample` / 6000.

## Command Line Arguments
`--num-processes` is an integer which specifies the thread count. The default value is 2.

`--cuda` is a boolean value which specifies whether the CUDA (GPU) given by a value of `True` or only the CPUs will be used given by a value of `False`. The default value is `False`.

`--batch-size` is an integer which specifies the size of the batch used in training. The default value is 64.

`--epochs` is an integer value which specifies the number of epochs or complete runs the training of the dataset will go through. The default value is 1.The experiment performed used the default value.

`--test-batch-size` is an integer value which specifies the size of the batch used for testing. The default value is 1. This is not used in the current implementation of the code because testing the CNN is not taking place. The experiment performed used the default value.

`--lr` is a floating point value which specifies the training learning rate. The default value is 0.01. The experiment performed used the default value.

`--momentum` is a floating point value which specifies the Stochastic Gradient Descent (optimizer) momentum. The default value is 0.5. The experiment performed used the default value.

`--seed` is an integer value which specifies the seed value used in the training process (and if being calculated the testing process). The default value is 1. The seed value was randomly choosen in this experiment.

`--log-interval` is an integer whch defines how many batches to wait before logging training status. The default value is 10. This is not used in this experiment.

`--dry-run` is a boolean value which tells the Python script to, in a single pass, perform a quick quality check. The default value is `False`. The experiment performed used the default value.

# Running the Code

## CNN MNIST Benchmark
This file was used to debug and benchmark CNNs with varying thread count, GPU vs CPU implementation, and varying batch size.

Usage:
```
python CNN_main.py --num-processes <int> --cuda <True | False> --batch-size <int>
```


## SNN MNIST Benchmark

This script runs all of the SNN variations used to record the data in this project.

Usage:

```
python snn_mnist_benchmark.py --device <cpu | gpu> --n_threads <int> --batch_size <int> [--rate_coding | -- temporal_coding]
```

## SNN MNIST Benchmark Scripts
These script runs all of the SNN variations used to record the data in this project.

Usage:

```
bash snn_mnist_benchmark_cpu.sh
bash snn_mnist_benchmark_gpu.sh
```

## SNN Custom Benchmark
This file was used to debug and benchmark SNNs with various layer and neuron counts.

Usage:
```
python snn_custom_benchmark.py --device <cpu | gpu> --n_threads <int> --recurrent <True | False> --n_neurons_per <int> --n_layers <int> --batch_size <int> [--rate_coding | -- temporal_coding]
```

## SNN Custom Benchmark Script

This script runs all of the SNN variations used to record the data in this project.

Usage:

```
bash snn_custom_benchmark.sh
```

## Running cProfile and SnakeViz for Analysis

cProfile and SnakeViz can be used to analyze the pythonn script execution using the following commands:

```
python -m -cProfile -o snn_mnist_profile.prof snn_mnist.py
python -m snakeviz snn_mnist_profile.prof
```