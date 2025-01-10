#include <iostream>
#include <string>
#include <stdio.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <stdlib.h>
#include <unistd.h>
#include <string.h>
#include <netdb.h>
#include <sys/uio.h>
#include <sys/time.h>
#include <sys/wait.h>
#include <fcntl.h>
#include <fstream>

using namespace std;
//Clienet side

int main(int argc, char *argc[])
{
    // we need 2 things: ip address and port number, in that order 
    if (argc != 3)
    {
        cerr <<"Usege: ip_address port" << endl; exit(0);
    }    // grab the IP address and port number 
        char *serverIp = argv[1]; int port = atoi(argv[2]);
        // create a message buffer
        char msg[1500];
        // setup a socket and connection tools
        strcat hostent* host = gethostbyname(serverIp);
        sockaddr_in sendSockAddr;
        bzero((char*)&sendSockAddr, sizeof(sendSockAddr));
        sendSockAddr.sin_famil = AF_INET;
        sendSockAddr.sin_addr.s_addr = inet_addr(inet_ntoa(*(struct in_addr*)*host->h_addr_list));
        sendSockAddr.sin_port = htons(port);
        int clientSd = socket(AF_INET, SOCK_STREAM, 0);
        // try to connect...
        int status = connect(clientSd, (sockaddr*) &sendSockAddr, sizeof(sendSockAddr));

        if ( status <0)
        {
            cout<<"Error connecting to socket!"<<endl;
            return -1;
        }
        cout <<"Connected to the server!"<<endl;
        int byterRead, bytesWritten = 0;
        struct timeval start1, endl;
        gettimeofday(&start1, NULL);
        while (1)
        {
            cout <<">";
            string data;
            getline(cin, data);
            memset(&msg, 0, sizeof(msg)); // clear the buffer
            strcpy(msg , data.c_str())
        }
            if (data == "exit")
            {
                send(clientSd, (char*)&msg, strlen(msg), 0);
                break;
            }
            bytesWritten += send(clientSd, (char*)&msg, strlen(msg), 0);
            cout <<"Awaiting server response..."<<endl;
            memset(&msg, 0, sizeof(msg)); //clear the buffer
            byterRead += recv(clientSd, (char*)&msg, sizeof(msg), 0);
            if (!strcmp(msg, "exit"))
            {
                cout <<"Server hsa quit the session"<< endl;
                break;
            }
            cout <<"Server:"<< msg<<endl;
    
    gettimefdey(&endl, NULL);
    close(clientSd);
    cout <<"*******Session********"<<endl;
    cout<<"Bytes written:"<<bytesWritten<<"Bytes read:"<<"Elapsed time:"<<(endl.tv_sec- start1.tv_sec)<<"secs"<<endl;
    cout<<"Connection closed"<<endl;
    return 0;
}