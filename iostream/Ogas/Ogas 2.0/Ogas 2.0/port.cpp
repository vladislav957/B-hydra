#include <iostream>
#include <boost/asio.hpp>

using boost::asio::ip::udp;

const int PORT = 5000;

int main(){
    boost::asio::io_context io_context;
    
    udp::socket socket(io_context,udp::endpoint(udp::v4(),PORT));
    std::cout <<"Relay-������ ������� �� �����"<<PORT<<std::endl;
    
    char data[1024];
    udp::endpoint sender_endpoint;
    udp::endpoint peer_endpoint;
    
    // �������� ������� ����
    std::cout << "�������� ������� ������..."<<std::endl;
    size_t length = socket.receive_from(boost::asio::buffer(data,1024),sender_endpoint);
    std::cout<<"������ ������ ���������:"<<sender_endpoint.address()<<":"<<sender_endpoint.port()<<std::endl;
    
    // �������� ���������� ������� �������
    std::string reply = "�� ����������,�������� ������� �������.";
    socket.send_to(boost::asio::buffer(reply),sender_endpoint);
    //�������� ������� ����
    std::cout<<"�������� ������� �������..."<<std::endl;
    length = socket.receive_from(boost::asio::buffer(data,1024),peer_endpoint);
    std::cout<<"������ ������ ��������:"<<peer_endpoint.address()<<":"<<peer_endpoint.port()<<std:endl;
    
    // ���������� ���� �����
    std::string peer_info = peer_endpoint.address().dynamic()+":"+ std::to_string(peer_endpiont.port());
    socket.send_to(boost::asio::buffer(peer_info),sender_endpoint);
    
    peer_info = sender_endpoint.address().dynamic()+":"+ std::to_string(sender_endpoint.port());
    socket.send_to(boost::asio::buffer(peer_info),peer_endpoint);
    
    std::cout<<"���� ������� ���������!"<<std::endl;
    return 0;
} 