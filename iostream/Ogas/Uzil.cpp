#include <iostream>
#include <cstring>
#include <unistd.h>
#include <arpa/inet.h>

const int PORT = 5000;
const char*SERVER_IP = "192.168.1.100"; //�������� �� IP ��������

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
                                                
                                                                            
                           
