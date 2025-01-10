#include <iostream>
#include <cstring>
#include <unistd.h>
#include <arpa/inet.h>
#include <Python.h>
#define SPAM_MODULE
#include "spammodule.h"

const int PORT = 5000;
const char*SERVER_IP = "192.168.1.100"; //�������� �� IP ��������

static int
PySpam_System(const char *command)
{
    return system(command);
}

int main(){
    struct sockaddr_in server_addr;
    char buffer[1024] = {0};
    
    //�������� ������
    if((client_fd = socket(AF_INET,SOCK_STREAM, 0))<0){
    perror("������ �������� ������");
    return 1;
                  
    }
                  
    server_addr.sin_family = AF_INET;
    server_addr.sin_port = htons(PORT);
                  
   //�������������� IP-������
   if(inet_pton(AF_INET,SERVER_IP,&server_addr.sin_addr)<=0){
    perror("�������� �����/���� �� ��������������");
    close(client_fd);
    return 1;
                  
    }
                  
    //����������� � �������
    if(connect(client_fd,(struct sockaddr*)&server_addr, sizeof(server_addr))<0){
    rerror("������ �����������");
    close(client_fd);
    return 1;
    }
                                               
    //�������� ������
    const char* message = "������ �� �������!";
    send(client_fd, message, strlen(message), 0);
    std::cout<<"��������� ����������."<<std::endl;
                                               
    //��������� ������
    read(cliennt_fd,buffer,1024);
    std::cout<<"����� �� �������:"<<buffer<<std::endl;
                                               
    close(client_fd);
    return 0;
                                               
    }
                                                
    static PyObject *
spam_system(PyObject *self, PyObject *args)
{
    const char *command;
    int sts;

    if (!PyArg_ParseTuple(args, "s", &command))
        return NULL;
    sts = PySpam_System(command);
    return PyLong_FromLong(sts);
}

#ifndef Py_SPAMMODULE_H
#define Py_SPAMMODULE_H
#ifdef __cplusplus
extern "C" {
#endif

/* Header file for spammodule */

/* C API functions */
#define PySpam_System_NUM 0
#define PySpam_System_RETURN int
#define PySpam_System_PROTO (const char *command)

/* Total number of C API pointers */
#define PySpam_API_pointers 1


#ifdef SPAM_MODULE
/* This section is used when compiling spammodule.c */

static PySpam_System_RETURN PySpam_System PySpam_System_PROTO;

#else
/* This section is used in modules that use spammodule's API */

static void **PySpam_API;

#define PySpam_System \
 (*(PySpam_System_RETURN (*)PySpam_System_PROTO) PySpam_API[PySpam_System_NUM])

/* Return -1 on error, 0 on success.
 * PyCapsule_Import will set an exception if there's an error.
 */
static int
import_spam(void)
{
    PySpam_API = (void **)PyCapsule_Import("spam._C_API", 0);
    return (PySpam_API != NULL) ? 0 : -1;
}

#endif

#ifdef __cplusplus
}
#endif

#endif /* !defined(Py_SPAMMODULE_H) */

PyMODINIT_FUNC
PyInit_client(void)
{
    PyObject *m;

    m = PyModule_Create(&clientmodule);
    if (m == NULL)
        return NULL;
    if (import_spam() < 0)
        return NULL;
    /* additional initialization can happen here */
    return m;
}