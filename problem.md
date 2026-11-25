Problem Statement:  Smart Traffic Management. Identify computational tasks involving data collection, monitoring, numerical computation, and data analysis (no prediction or machine learning required).

Using the Ubuntu virtual machine and Docker environment created in Assignment 1, implement two Docker containers representing an Edge Node and a Cloud Node.

Develop an application to distribute computational tasks between these containers. Establish communication between them to transfer task data from the Edge Node to the Cloud Node and return the processed results.

Implement and simulate the following three computational offloading strategies: Local Execution (No Offloading): Execute the complete task within the Edge container. Full Offloading: Transfer the complete computational task from the Edge container to the Cloud container for execution. Partial Offloading: Divide a computational task into multiple parts and execute some parts at the Edge and the remaining parts in the Cloud. Group must demonstrate when,why, and how computational tasks are offloaded between Edge and Cloud nodes.

Performance Evaluation: Evaluate and compare the three offloading strategies using: Task execution time. End-to-end response time.

Provide a brief analysis of the experimental observations.

### Expected Deliverables

Description of the selected IoT application and identified computational tasks. Dockerfiles and source code for Edge and Cloud containers. Implementation of all three offloading strategies. Demonstration of task transfer and communication between containers. Performance evaluation and comparison. Brief report containing observations and conclusions. Tools and Technologies Required Virtualization: Oracle VirtualBox / VMware / Hyper-V. Operating System: Ubuntu Linux. Containerization: Docker. Programming Language: Python 3. Communication: Python Flask **REST** **API**. Data Transfer: **JSON** over **HTTP**. Performance Measurement: Python time module and Docker Stats. Required Python Libraries: Flask: To develop **REST** APIs for Edge–Cloud communication. Requests: To transfer computational tasks from the Edge container to the Cloud container. ### Submission Guidelines Submit a **PDF** report containing the aim, selected IoT application, computational task description, tools used, system architecture, procedure, Docker commands, source code, screenshots of Edge and Cloud containers, demonstration of Local, Full, and Partial Offloading, performance comparison (execution time and response time), observations, and conclusion. Submit the Python source code as a **ZIP** file. **PDF** File name: Group_<Number>_Assignment2.pdf Source code **ZIP** file name: Group_<Number>_SourceCode_Assignment2.zip